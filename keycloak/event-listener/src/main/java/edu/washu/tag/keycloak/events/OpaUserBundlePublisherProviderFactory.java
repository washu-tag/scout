package edu.washu.tag.keycloak.events;

import java.net.URI;
import java.net.http.HttpClient;
import java.time.Duration;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;
import java.util.stream.Collectors;
import org.jboss.logging.Logger;
import org.keycloak.Config;
import org.keycloak.events.EventListenerProviderFactory;
import org.keycloak.models.GroupModel;
import org.keycloak.models.KeycloakSession;
import org.keycloak.models.KeycloakSessionFactory;
import org.keycloak.models.RealmModel;
import org.keycloak.models.utils.KeycloakModelUtils;
import org.keycloak.utils.StringUtil;

/**
 * Owns the long-lived state for OPA bundle publishing: the in-memory user
 * snapshot, the debounce scheduler, and the MinIO uploader. Per ADR 0021,
 * this is the v2 follow-up to the cache-bust event listener — instead of
 * telling OPA "alice's cache entry is stale," we maintain an authoritative
 * snapshot and publish it as an OPA bundle on changes. OPA's native bundle
 * plugin handles distribution to all replicas.
 *
 * <p>Lifecycle:
 * <ul>
 *   <li>{@link #init} reads MinIO config from env vars and constructs the
 *       uploader. No I/O at this stage.</li>
 *   <li>{@link #postInit} walks the configured realm's users to populate
 *       the in-memory map and publishes the initial bundle. Runs once
 *       per Keycloak instance.</li>
 *   <li>{@link #create} returns a thin per-session provider that forwards
 *       events back to this factory's mutation methods.</li>
 *   <li>{@link #close} shuts down the scheduler.</li>
 * </ul>
 *
 * <p>Thread safety: the user map is a {@link ConcurrentHashMap}. The
 * scheduler runs publishes on a single thread, so two concurrent
 * publishes can't interleave. A copy of the map is taken at the start
 * of each publish to avoid mutation during JSON serialization.
 */
public class OpaUserBundlePublisherProviderFactory implements EventListenerProviderFactory {

    public static final String ID = "opa-user-bundle-publisher";

    // Env var names — Keycloak operator's additionalOptions block in
    // ansible/roles/keycloak/tasks/deploy.yaml renders these from the
    // option names by prefixing KC_ and uppercasing.
    static final String ENV_ENDPOINT = "KC_OPA_BUNDLE_S3_ENDPOINT";
    static final String ENV_BUCKET = "KC_OPA_BUNDLE_S3_BUCKET";
    static final String ENV_OBJECT_KEY = "KC_OPA_BUNDLE_S3_OBJECT_KEY";
    static final String ENV_REGION = "KC_OPA_BUNDLE_S3_REGION";
    static final String ENV_ACCESS_KEY = "KC_OPA_BUNDLE_S3_ACCESS_KEY";
    static final String ENV_SECRET_KEY = "KC_OPA_BUNDLE_S3_SECRET_KEY";
    static final String ENV_REALM = "KC_OPA_BUNDLE_REALM";
    static final String ENV_DEBOUNCE_MS = "KC_OPA_BUNDLE_DEBOUNCE_MS";

    private static final long DEFAULT_DEBOUNCE_MS = 1_000L;
    private static final long INITIAL_PUBLISH_DELAY_MS = 5_000L;
    private static final long RETRY_DELAY_MS = 30_000L;

    private static final Logger log = Logger.getLogger(OpaUserBundlePublisherProviderFactory.class);

    private final ConcurrentHashMap<String, Map<String, Object>> users = new ConcurrentHashMap<>();
    // userId -> last-known username. Keycloak admin UPDATE events don't carry
    // the previous username, so without this we can't drop the stale entry
    // when a user is renamed — the bundle would keep both names, and a later
    // user recycling the old username would inherit the renamed user's attrs.
    private final ConcurrentHashMap<String, String> userIdToUsername = new ConcurrentHashMap<>();
    private final AtomicReference<ScheduledFuture<?>> pendingPublish = new AtomicReference<>();

    private MinioBundleUploader uploader;
    private ScheduledExecutorService scheduler;
    private String realmName;
    private long debounceMs;
    private boolean enabled;

    @Override
    public OpaUserBundlePublisherProvider create(KeycloakSession session) {
        return new OpaUserBundlePublisherProvider(session, this);
    }

    @Override
    public void init(Config.Scope config) {
        String endpoint = System.getenv(ENV_ENDPOINT);
        String bucket = System.getenv(ENV_BUCKET);
        String accessKey = System.getenv(ENV_ACCESS_KEY);
        String secretKey = System.getenv(ENV_SECRET_KEY);

        if (StringUtil.isBlank(endpoint) || StringUtil.isBlank(bucket)
                || StringUtil.isBlank(accessKey) || StringUtil.isBlank(secretKey)) {
            log.infof("OPA bundle publisher disabled: one of %s/%s/%s/%s is unset",
                    ENV_ENDPOINT, ENV_BUCKET, ENV_ACCESS_KEY, ENV_SECRET_KEY);
            enabled = false;
            return;
        }

        String objectKey = orDefault(System.getenv(ENV_OBJECT_KEY), "scout/bundle.tar.gz");
        String region = orDefault(System.getenv(ENV_REGION), "us-east-1");
        realmName = orDefault(System.getenv(ENV_REALM), "scout");
        debounceMs = parseLong(System.getenv(ENV_DEBOUNCE_MS), DEFAULT_DEBOUNCE_MS);

        HttpClient httpClient = HttpClient.newBuilder()
                .connectTimeout(Duration.ofSeconds(5))
                .build();
        uploader = new MinioBundleUploader(httpClient, URI.create(endpoint), bucket,
                objectKey, accessKey, secretKey, region);
        scheduler = Executors.newSingleThreadScheduledExecutor(r -> {
            Thread t = new Thread(r, "opa-bundle-publisher");
            t.setDaemon(true);
            return t;
        });
        enabled = true;
        log.infof("OPA bundle publisher enabled: endpoint=%s bucket=%s object=%s realm=%s",
                endpoint, bucket, objectKey, realmName);
    }

    @Override
    public void postInit(KeycloakSessionFactory factory) {
        if (!enabled) {
            return;
        }
        // Delay the initial walk: realms aren't always ready immediately
        // after postInit fires (especially during fresh deploys where the
        // realm import runs in parallel), and we don't want to hammer
        // Keycloak before it's stabilized. 5s is enough to clear the
        // common startup contention without making cold-start painful.
        scheduler.schedule(() -> seedFromKeycloak(factory), INITIAL_PUBLISH_DELAY_MS, TimeUnit.MILLISECONDS);
    }

    @Override
    public void close() {
        if (scheduler != null) {
            scheduler.shutdownNow();
        }
    }

    @Override
    public String getId() {
        return ID;
    }

    // === Mutation API (called from per-session provider) =================

    void upsertUser(String userId, String username, boolean userEnabled, List<String> groups,
                    Map<String, List<String>> attributes) {
        if (!enabled || username == null) {
            return;
        }
        // Rename detection: if this userId was last seen under a different
        // username, drop the stale record before writing the new one.
        if (userId != null) {
            String previous = userIdToUsername.put(userId, username);
            if (previous != null && !previous.equals(username)) {
                users.remove(previous);
            }
        }
        users.put(username, BundleAssembler.userPayload(userEnabled, groups, attributes));
        scheduleDebouncedPublish();
    }

    void removeUser(String userId, String username) {
        if (!enabled) {
            return;
        }
        // Prefer the username carried by the DELETE event; fall back to the
        // userId->username map when the event omits it (some transports strip
        // event details), so the entry still gets removed.
        String resolved = username;
        if (userId != null) {
            String mapped = userIdToUsername.remove(userId);
            if (resolved == null) {
                resolved = mapped;
            }
        }
        if (resolved == null) {
            return;
        }
        users.remove(resolved);
        scheduleDebouncedPublish();
    }

    boolean isEnabled() {
        return enabled;
    }

    // === Test seam (package-private) ======================================
    // Lets OpaUserBundlePublisherProviderFactoryTest exercise upsertUser /
    // removeUser without env vars or a live MinIO. Those methods gate on
    // `enabled` and schedule a debounced publish, so a test needs both set;
    // pass a Mockito mock scheduler so no real publish fires. The
    // rename/remove bookkeeping mutates the in-memory maps synchronously
    // before scheduling, which is what the tests assert on.
    void enableForTest(ScheduledExecutorService testScheduler) {
        this.enabled = true;
        this.scheduler = testScheduler;
        this.debounceMs = DEFAULT_DEBOUNCE_MS;
    }

    Map<String, Map<String, Object>> usersForTest() {
        return users;
    }

    String mappedUsernameForTest(String userId) {
        return userIdToUsername.get(userId);
    }

    // === Internals ========================================================

    private void scheduleDebouncedPublish() {
        // Coalesce a burst of admin events into one S3 PUT.
        schedulePublish(debounceMs);
    }

    private void schedulePublish(long delayMs) {
        // Track the scheduled publish in pendingPublish (canceling any prior
        // pending one) so it can be coalesced. The scheduler is single-
        // threaded, so the run() that actually fires sees the latest
        // pendingPublish reference. Both the debounce path and the
        // failed-upload retry go through here — otherwise an untracked retry
        // can't be canceled by a subsequent event, and a sustained MinIO
        // outage with steady admin traffic accumulates a retry per failed
        // publish that all fire after recovery.
        ScheduledFuture<?> next = scheduler.schedule(this::publishNow, delayMs, TimeUnit.MILLISECONDS);
        ScheduledFuture<?> prev = pendingPublish.getAndSet(next);
        if (prev != null) {
            prev.cancel(false);
        }
    }

    private void publishNow() {
        Map<String, Map<String, Object>> snapshot = new HashMap<>(users);
        long revision = System.currentTimeMillis();
        // Catch Throwable, not just Exception: a missing runtime dep
        // (e.g. commons-compress not shipped in Keycloak's providers/)
        // raises NoClassDefFoundError, which is an Error subclass. With
        // only `catch (Exception)`, the scheduler's worker thread dies
        // silently and the publish becomes a black hole — we hit
        // exactly that during initial rollout. Catching Throwable
        // gives us the diagnostic, at the cost of also catching things
        // like OutOfMemoryError (acceptable for a single-threaded
        // scheduler that we don't want to lose).
        try {
            byte[] body = BundleAssembler.build(snapshot, revision);
            boolean ok = uploader.upload(body);
            if (!ok) {
                log.warnf("OPA bundle publish failed; retrying in %dms", RETRY_DELAY_MS);
                schedulePublish(RETRY_DELAY_MS);
            }
        } catch (Throwable t) {
            log.errorf(t, "OPA bundle assembly failed; %d users in snapshot", snapshot.size());
        }
    }

    private void seedFromKeycloak(KeycloakSessionFactory factory) {
        // Only publish once a full realm walk has succeeded. A failed walk
        // (realm not yet imported on a fresh deploy, transient DB error, or
        // an Error like NoClassDefFoundError) must NOT publish — an empty or
        // partial snapshot would drive OPA to deny every user until the next
        // admin event happens to repopulate the map, and realm-import
        // completion does not itself fire user-level admin events.
        AtomicBoolean seeded = new AtomicBoolean(false);
        try {
            KeycloakModelUtils.runJobInTransaction(factory, session -> {
                RealmModel realm = session.realms().getRealmByName(realmName);
                if (realm == null) {
                    log.warnf("OPA bundle seed: realm %s not found; retrying in %dms", realmName, RETRY_DELAY_MS);
                    return;
                }
                // runJobInTransaction opens a fresh session but doesn't
                // bind it to a realm. The user provider's validateUser
                // path resolves the realm via session.getContext()
                // (InfinispanOrganizationProvider.getRealm), so without
                // an explicit setRealm() we get "Session not bound to a
                // realm" mid-stream.
                session.getContext().setRealm(realm);
                session.users().searchForUserStream(realm, Map.of()).forEach(user -> {
                    String username = user.getUsername();
                    if (username == null) {
                        return;
                    }
                    if (user.getId() != null) {
                        userIdToUsername.put(user.getId(), username);
                    }
                    List<String> groups = user.getGroupsStream()
                            .map(GroupModel::getName)
                            .collect(Collectors.toList());
                    Map<String, Object> payload = BundleAssembler.userPayload(
                            user.isEnabled(), groups, user.getAttributes());
                    users.put(username, payload);
                });
                log.infof("OPA bundle seed: loaded %d users from realm %s", users.size(), realmName);
                seeded.set(true);
            });
        } catch (Throwable t) {
            // Throwable, not Exception: a missing runtime dep raises
            // NoClassDefFoundError (an Error), which a bare catch(Exception)
            // would miss — same hardening as publishNow below.
            log.errorf(t, "OPA bundle seed walk failed; retrying in %dms", RETRY_DELAY_MS);
        }
        if (seeded.get()) {
            // A successful walk over a genuinely empty realm publishes an
            // empty bundle, which is correct — there are no users to allow.
            publishNow();
        } else {
            scheduler.schedule(() -> seedFromKeycloak(factory), RETRY_DELAY_MS, TimeUnit.MILLISECONDS);
        }
    }

    private static String orDefault(String value, String fallback) {
        return (value == null || value.isBlank()) ? fallback : value;
    }

    private static long parseLong(String value, long fallback) {
        if (value == null || value.isBlank()) {
            return fallback;
        }
        try {
            return Long.parseLong(value.trim());
        } catch (NumberFormatException e) {
            return fallback;
        }
    }
}
