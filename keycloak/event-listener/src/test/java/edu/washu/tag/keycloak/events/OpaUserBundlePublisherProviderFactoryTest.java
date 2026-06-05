package edu.washu.tag.keycloak.events;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyBoolean;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.doReturn;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;

import java.util.List;
import java.util.Map;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;

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

    @Test
    void debounce_coalesces_pending_publishes() {
        // Each mutation schedules a publish and cancels the prior pending one,
        // so a burst of admin events collapses to a single S3 PUT. Drop the
        // cancel-prior and a sustained event stream fires a publish per event
        // (ADR 0021 calls this out as the subtle failure mode). Uses a mock
        // scheduler returning distinct futures and asserts the prior is
        // cancelled when the next is scheduled.
        ScheduledExecutorService sched = mock(ScheduledExecutorService.class);
        ScheduledFuture<?> first = mock(ScheduledFuture.class);
        ScheduledFuture<?> second = mock(ScheduledFuture.class);
        doReturn(first, second).when(sched)
                .schedule(any(Runnable.class), anyLong(), any(TimeUnit.class));
        factory.enableForTest(sched);

        upsert("id-1", "alice", "WUSM");
        upsert("id-2", "bob", "BJH");

        verify(sched, times(2)).schedule(any(Runnable.class), anyLong(), eq(TimeUnit.MILLISECONDS));
        verify(first).cancel(false); // the prior pending publish is coalesced
        verify(second, never()).cancel(anyBoolean());
    }

    @Test
    void failed_upload_reschedules_retry() {
        // publishNow runs, the upload fails, and a retry must be scheduled at
        // RETRY_DELAY_MS (30s) — otherwise a transient MinIO outage drops the
        // bundle update permanently. Capture the debounced Runnable and run it.
        ScheduledExecutorService sched = mock(ScheduledExecutorService.class);
        MinioBundleUploader uploader = mock(MinioBundleUploader.class);
        doReturn(false).when(uploader).upload(any());
        ArgumentCaptor<Runnable> task = ArgumentCaptor.forClass(Runnable.class);
        doReturn(mock(ScheduledFuture.class)).when(sched)
                .schedule(task.capture(), anyLong(), any(TimeUnit.class));
        factory.enableForTest(sched, uploader);

        upsert("id-1", "alice", "WUSM"); // schedules the debounced publish
        task.getValue().run(); // run it: upload fails -> reschedule retry

        verify(sched).schedule(any(Runnable.class), eq(30_000L), eq(TimeUnit.MILLISECONDS));
    }

    @Test
    void successful_upload_does_not_reschedule() {
        // The mirror of the retry test: a successful publish must NOT schedule a
        // retry, or every publish would double-fire.
        ScheduledExecutorService sched = mock(ScheduledExecutorService.class);
        MinioBundleUploader uploader = mock(MinioBundleUploader.class);
        doReturn(true).when(uploader).upload(any());
        ArgumentCaptor<Runnable> task = ArgumentCaptor.forClass(Runnable.class);
        doReturn(mock(ScheduledFuture.class)).when(sched)
                .schedule(task.capture(), anyLong(), any(TimeUnit.class));
        factory.enableForTest(sched, uploader);

        upsert("id-1", "alice", "WUSM");
        task.getValue().run(); // upload succeeds -> no retry

        verify(sched, never()).schedule(any(Runnable.class), eq(30_000L), eq(TimeUnit.MILLISECONDS));
    }
}
