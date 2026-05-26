package edu.washu.tag.keycloak.events;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.Mockito.mock;

import java.util.List;
import java.util.Map;
import java.util.concurrent.ScheduledExecutorService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

/**
 * Unit tests for the factory's in-memory snapshot bookkeeping —
 * specifically the userId-keyed rename detection and the delete-by-id
 * fallback. Keycloak admin UPDATE events don't carry the previous
 * username, and DELETE events can arrive without a username in their
 * details; both paths rely on the userId -> username map to keep the
 * bundle from accumulating stale entries.
 *
 * <p>The factory is driven through its package-private {@code enableForTest}
 * seam with a mock scheduler, so {@code scheduleDebouncedPublish} is inert
 * and no MinIO upload fires. The mutation methods write the in-memory maps
 * synchronously before scheduling, so assertions run right after the call.
 */
class OpaUserBundlePublisherProviderFactoryTest {

    private OpaUserBundlePublisherProviderFactory factory;

    @BeforeEach
    void setUp() {
        factory = new OpaUserBundlePublisherProviderFactory();
        factory.enableForTest(mock(ScheduledExecutorService.class));
    }

    private void upsert(String userId, String username, String facility) {
        factory.upsertUser(
                userId,
                username,
                true,
                List.of("scout-user"),
                Map.of("allowed_facilities", List.of(facility)));
    }

    @Test
    void rename_drops_stale_username_entry() {
        upsert("id-1", "alice", "WUSM");
        assertTrue(factory.usersForTest().containsKey("alice"));

        // Same userId, new username — an admin renamed the user. The
        // UPDATE event carries only the new name, so without userId
        // bookkeeping the old "alice" entry would linger.
        upsert("id-1", "alice2", "WUSM");

        assertFalse(factory.usersForTest().containsKey("alice"),
                "renamed-away username must be dropped from the snapshot");
        assertTrue(factory.usersForTest().containsKey("alice2"));
        assertEquals(1, factory.usersForTest().size());
        assertEquals("alice2", factory.mappedUsernameForTest("id-1"));
    }

    @Test
    void recycled_username_does_not_inherit_renamed_users_attributes() {
        // The security-relevant case: user id-1 is "alice" with WUSM, then
        // renamed to "alice_old". A *different* user id-2 later takes the
        // freed-up "alice" username with BJH. The new "alice" must carry
        // id-2's attributes, not id-1's stale WUSM scoping.
        upsert("id-1", "alice", "WUSM");
        upsert("id-1", "alice_old", "WUSM");
        upsert("id-2", "alice", "BJH");

        @SuppressWarnings("unchecked")
        List<String> facilities =
                (List<String>) factory.usersForTest().get("alice").get("allowed_facilities");
        assertEquals(List.of("BJH"), facilities,
                "recycled username must reflect the new user's attributes");
    }

    @Test
    void same_user_same_username_updates_in_place() {
        upsert("id-1", "alice", "WUSM");
        upsert("id-1", "alice", "BJH");

        assertEquals(1, factory.usersForTest().size());
        @SuppressWarnings("unchecked")
        List<String> facilities =
                (List<String>) factory.usersForTest().get("alice").get("allowed_facilities");
        assertEquals(List.of("BJH"), facilities);
    }

    @Test
    void delete_with_username_removes_entry() {
        upsert("id-1", "alice", "WUSM");

        factory.removeUser("id-1", "alice");

        assertFalse(factory.usersForTest().containsKey("alice"));
        assertNull(factory.mappedUsernameForTest("id-1"),
                "userId mapping should be cleaned up on delete");
    }

    @Test
    void delete_without_username_falls_back_to_id_map() {
        // Some event transports strip the DELETE event's details, so the
        // username is null. The userId -> username map is the fallback.
        upsert("id-1", "alice", "WUSM");

        factory.removeUser("id-1", null);

        assertFalse(factory.usersForTest().containsKey("alice"),
                "delete must resolve the username via the userId map when the event omits it");
    }

    @Test
    void delete_without_username_or_mapping_is_noop() {
        // No prior upsert for this id and no username on the event: nothing
        // to resolve, so the call is a safe no-op (no exception, no spurious
        // removal of an unrelated entry).
        upsert("id-1", "alice", "WUSM");

        factory.removeUser("id-unknown", null);

        assertTrue(factory.usersForTest().containsKey("alice"),
                "an unresolvable delete must not touch other entries");
        assertEquals(1, factory.usersForTest().size());
    }
}
