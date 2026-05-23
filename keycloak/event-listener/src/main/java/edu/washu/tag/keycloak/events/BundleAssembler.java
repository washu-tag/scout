package edu.washu.tag.keycloak.events;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.zip.GZIPOutputStream;
import org.apache.commons.compress.archivers.tar.TarArchiveEntry;
import org.apache.commons.compress.archivers.tar.TarArchiveOutputStream;
import org.keycloak.util.JsonSerialization;

/**
 * Builds an OPA bundle as a gzipped tar from a user-attribute snapshot.
 *
 * <p>Bundle layout matches OPA's expectations:
 * <pre>
 * bundle.tar.gz
 * ├── .manifest          # { "revision": "&lt;epoch_ms&gt;", "roots": ["users"] }
 * └── users/
 *     └── data.json      # { "&lt;username&gt;": {...attrs...}, ... }
 * </pre>
 *
 * <p>The {@code roots} declaration scopes atomic data-document swaps to
 * {@code data.users}, so this bundle doesn't collide with the static data
 * ({@code filtered_tables}, {@code view_only_tables}, etc.) rendered into
 * OPA's ConfigMap by the Ansible role.
 */
final class BundleAssembler {

    private BundleAssembler() {
    }

    /**
     * Render a snapshot of user attributes as a bundle tar.gz.
     *
     * @param users    map keyed by username, value is the attribute payload
     *                 (Keycloak's {@code Map&lt;String, List&lt;String&gt;&gt;}
     *                 plus a top-level {@code enabled} boolean)
     * @param revision a monotonic revision identifier (epoch millis works)
     * @return the bytes of the gzipped tar
     */
    static byte[] build(Map<String, Map<String, Object>> users, long revision) throws IOException {
        byte[] dataJson = JsonSerialization.writeValueAsBytes(users);
        // LinkedHashMap to keep the manifest field order stable in the
        // serialized form — easier to diff if anybody inspects the bundle
        // contents directly with `tar -xOf bundle.tar.gz .manifest`.
        Map<String, Object> manifest = new LinkedHashMap<>();
        manifest.put("revision", Long.toString(revision));
        manifest.put("roots", List.of("users"));
        byte[] manifestJson = JsonSerialization.writeValueAsBytes(manifest);

        ByteArrayOutputStream out = new ByteArrayOutputStream();
        try (GZIPOutputStream gz = new GZIPOutputStream(out);
             TarArchiveOutputStream tar = new TarArchiveOutputStream(gz)) {
            writeEntry(tar, ".manifest", manifestJson);
            writeEntry(tar, "users/data.json", dataJson);
            tar.finish();
        }
        return out.toByteArray();
    }

    private static void writeEntry(TarArchiveOutputStream tar, String name, byte[] content)
            throws IOException {
        TarArchiveEntry entry = new TarArchiveEntry(name);
        entry.setSize(content.length);
        tar.putArchiveEntry(entry);
        tar.write(content);
        tar.closeArchiveEntry();
    }

    /**
     * Extract the per-user attribute payload from a Keycloak UserModel-shaped
     * input. Keycloak stores user attributes as {@code Map&lt;String, List&lt;String&gt;&gt;}
     * (multivalued by default); we pass that through verbatim plus add two
     * synthesized top-level fields:
     * <ul>
     *   <li>{@code enabled} — boolean, from {@code UserModel.isEnabled()}.</li>
     *   <li>{@code groups} — list of group names from
     *       {@code UserModel.getGroupsStream()}, used by the rego policy's
     *       approval gate (membership in {@code data.approved_groups}).</li>
     * </ul>
     * The rego policy reads attributes as arrays
     * ({@code user_attrs.allowed_facilities[0]}, etc.), so preserving the
     * shape here means no schema in two places.
     *
     * @param enabled    {@link org.keycloak.models.UserModel#isEnabled()}
     * @param groups     list of group names the user belongs to; never null
     * @param attributes {@link org.keycloak.models.UserModel#getAttributes()}
     * @return a payload map suitable for inclusion under
     *         {@code data.users.&lt;username&gt;} in the bundle
     */
    static Map<String, Object> userPayload(
            boolean enabled, List<String> groups, Map<String, List<String>> attributes) {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("enabled", enabled);
        payload.put("groups", groups != null ? groups : List.of());
        if (attributes != null) {
            // Filter out Keycloak's internal attribute keys (username,
            // firstName, lastName, etc. — present in the attributes map
            // because Keycloak exposes them through the same accessor as
            // user-defined RBAC attributes). Anything starting with "kc."
            // is also internal. The rego policy only reads the explicitly
            // configured attribute names from data.attribute_filters and
            // mask_phi_fields, so over-including is harmless but bloats
            // the bundle unnecessarily for every user.
            for (Map.Entry<String, List<String>> e : attributes.entrySet()) {
                String key = e.getKey();
                if (isInternalAttribute(key)) {
                    continue;
                }
                payload.put(key, e.getValue());
            }
        }
        return payload;
    }

    private static boolean isInternalAttribute(String key) {
        if (key == null) {
            return true;
        }
        if (key.startsWith("kc.")) {
            return true;
        }
        // Keycloak's user-profile system surfaces these as "attributes"
        // on top of the columns of the same name. Skip to keep the
        // bundle focused on RBAC-relevant fields.
        return switch (key) {
            case "username", "email", "firstName", "lastName" -> true;
            default -> false;
        };
    }
}
