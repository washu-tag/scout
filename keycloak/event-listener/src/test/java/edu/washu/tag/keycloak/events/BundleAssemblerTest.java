package edu.washu.tag.keycloak.events;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.zip.GZIPInputStream;
import org.apache.commons.compress.archivers.tar.TarArchiveEntry;
import org.apache.commons.compress.archivers.tar.TarArchiveInputStream;
import org.junit.jupiter.api.Test;

/**
 * Round-trip tests: build a bundle, decompress + untar it, verify the
 * .manifest and users/data.json look the way OPA expects.
 */
class BundleAssemblerTest {

    private static final ObjectMapper JSON = new ObjectMapper();

    @Test
    void bundle_contains_manifest_and_users_data_json() throws IOException {
        Map<String, Map<String, Object>> users = new HashMap<>();
        Map<String, Object> alice = new LinkedHashMap<>();
        alice.put("enabled", true);
        alice.put("allowed_facilities", List.of("WUSM"));
        users.put("alice", alice);

        byte[] bundle = BundleAssembler.build(users, 1716393600000L);
        Map<String, byte[]> entries = readBundle(bundle);

        assertTrue(entries.containsKey(".manifest"), "bundle must contain .manifest");
        assertTrue(entries.containsKey("users/data.json"), "bundle must contain users/data.json");

        JsonNode manifest = JSON.readTree(entries.get(".manifest"));
        assertEquals("1716393600000", manifest.get("revision").asText());
        assertEquals(1, manifest.get("roots").size());
        assertEquals("users", manifest.get("roots").get(0).asText());

        JsonNode data = JSON.readTree(entries.get("users/data.json"));
        assertEquals(true, data.get("alice").get("enabled").asBoolean());
        assertEquals("WUSM", data.get("alice").get("allowed_facilities").get(0).asText());
    }

    @Test
    void user_payload_includes_enabled_groups_and_attributes() {
        Map<String, List<String>> attrs = new LinkedHashMap<>();
        attrs.put("allowed_facilities", List.of("WUSM", "BJH"));
        attrs.put("mask_phi_fields", List.of("false"));

        Map<String, Object> payload = BundleAssembler.userPayload(
                true, List.of("scout-user"), attrs);

        assertEquals(true, payload.get("enabled"));
        assertEquals(List.of("scout-user"), payload.get("groups"));
        assertEquals(List.of("WUSM", "BJH"), payload.get("allowed_facilities"));
        assertEquals(List.of("false"), payload.get("mask_phi_fields"));
    }

    @Test
    void user_payload_groups_default_to_empty_list_when_null() {
        // The provider should always pass a non-null list, but be defensive
        // here so a misuse doesn't produce a malformed bundle.
        Map<String, Object> payload = BundleAssembler.userPayload(true, null, Map.of());
        assertEquals(List.of(), payload.get("groups"));
    }

    @Test
    void user_payload_excludes_keycloak_internal_attributes() {
        // Keycloak's user-profile system surfaces these as "attributes"
        // alongside user-defined AuthZ fields. Including them bloats the
        // bundle and could leak PII (email, names). The rego policy
        // doesn't read them, so they're explicitly filtered out.
        Map<String, List<String>> attrs = new LinkedHashMap<>();
        attrs.put("allowed_facilities", List.of("WUSM"));
        attrs.put("email", List.of("alice@example.org"));
        attrs.put("firstName", List.of("Alice"));
        attrs.put("kc.something.internal", List.of("ignore"));

        Map<String, Object> payload = BundleAssembler.userPayload(
                true, List.of("scout-user"), attrs);

        assertTrue(payload.containsKey("allowed_facilities"));
        assertFalse(payload.containsKey("email"), "email should be excluded");
        assertFalse(payload.containsKey("firstName"), "firstName should be excluded");
        assertFalse(payload.containsKey("kc.something.internal"), "kc.* should be excluded");
    }

    @Test
    void disabled_user_serialized_as_enabled_false() {
        Map<String, Object> payload = BundleAssembler.userPayload(false,
                List.of("scout-user"), Map.of("allowed_facilities", List.of("WUSM")));
        assertEquals(false, payload.get("enabled"));
    }

    @Test
    void empty_users_map_produces_valid_bundle() throws IOException {
        // Cold-start before any user has been ingested. The bundle is
        // still well-formed; OPA's data.users is just {}.
        byte[] bundle = BundleAssembler.build(Map.of(), 1L);
        Map<String, byte[]> entries = readBundle(bundle);

        JsonNode data = JSON.readTree(entries.get("users/data.json"));
        assertNotNull(data);
        assertEquals(0, data.size());
    }

    private Map<String, byte[]> readBundle(byte[] tarGz) throws IOException {
        Map<String, byte[]> out = new LinkedHashMap<>();
        try (GZIPInputStream gz = new GZIPInputStream(new ByteArrayInputStream(tarGz));
             TarArchiveInputStream tar = new TarArchiveInputStream(gz)) {
            TarArchiveEntry entry;
            while ((entry = tar.getNextEntry()) != null) {
                if (entry.isDirectory()) {
                    continue;
                }
                byte[] buf = new byte[(int) entry.getSize()];
                int read = 0;
                while (read < buf.length) {
                    int n = tar.read(buf, read, buf.length - read);
                    if (n < 0) {
                        break;
                    }
                    read += n;
                }
                out.put(entry.getName(), buf);
            }
        }
        return out;
    }
}
