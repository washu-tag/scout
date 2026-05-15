package edu.wustl.scout.xnat.auth;

import lombok.extern.slf4j.Slf4j;
import org.nrg.framework.annotations.XnatPlugin;
import org.springframework.context.annotation.ComponentScan;
import org.springframework.security.config.annotation.web.configuration.EnableWebSecurity;

@XnatPlugin(value = "xnatScoutAuthPlugin", name = "XNAT Scout Auth Plugin")
@EnableWebSecurity
@ComponentScan({"edu.wustl.scout.xnat.auth"})
@Slf4j
public class ScoutAuthPlugin {
    public ScoutAuthPlugin() {
        log.info("xnat-scout-auth-plugin loaded");
    }
}
