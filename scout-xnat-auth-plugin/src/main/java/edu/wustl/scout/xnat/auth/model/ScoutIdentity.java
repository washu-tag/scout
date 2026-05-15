package edu.wustl.scout.xnat.auth.model;

import java.util.Collections;
import java.util.List;
import java.util.Objects;

/**
 * Normalized identity passed to UserProvisioningService. Both the header-trust
 * and bearer-token paths build one of these and hand it to the same provisioning
 * code so the user-mapping logic lives in one place.
 */
public final class ScoutIdentity {
    private final String sub;
    private final String preferredUsername;
    private final String email;
    private final String firstName;
    private final String lastName;
    private final List<String> groups;
    private final List<String> roles;

    public ScoutIdentity(String sub, String preferredUsername, String email,
                         String firstName, String lastName,
                         List<String> groups, List<String> roles) {
        this.sub = sub;
        this.preferredUsername = preferredUsername;
        this.email = email;
        this.firstName = firstName;
        this.lastName = lastName;
        this.groups = groups != null ? groups : Collections.emptyList();
        this.roles = roles != null ? roles : Collections.emptyList();
    }

    public String getSub() { return sub; }
    public String getPreferredUsername() { return preferredUsername; }
    public String getEmail() { return email; }
    public String getFirstName() { return firstName; }
    public String getLastName() { return lastName; }
    public List<String> getGroups() { return groups; }
    public List<String> getRoles() { return roles; }

    public boolean hasRole(String role) {
        return roles.contains(role);
    }

    public boolean hasGroup(String group) {
        return groups.contains(group);
    }

    @Override
    public String toString() {
        return "ScoutIdentity{sub=" + sub + ", preferred_username=" + preferredUsername
                + ", email=" + email + ", roles=" + roles + ", groups=" + groups + "}";
    }

    @Override
    public boolean equals(Object o) {
        if (this == o) return true;
        if (!(o instanceof ScoutIdentity)) return false;
        ScoutIdentity that = (ScoutIdentity) o;
        return Objects.equals(sub, that.sub)
                && Objects.equals(preferredUsername, that.preferredUsername)
                && Objects.equals(email, that.email)
                && Objects.equals(roles, that.roles)
                && Objects.equals(groups, that.groups);
    }

    @Override
    public int hashCode() {
        return Objects.hash(sub, preferredUsername, email, roles, groups);
    }
}
