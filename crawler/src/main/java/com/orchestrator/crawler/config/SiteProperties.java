package com.orchestrator.crawler.config;

import lombok.Data;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Component;

import java.util.HashMap;

@Component
@ConfigurationProperties(prefix = "sites")
public class SiteProperties extends HashMap<String, SiteProperties.SiteConfig> {

    @Data
    public static class SiteConfig {
        private String endpoint;
        private int pageSize = 20;
        private int maxPages = 100;
        private int intervalMs = 1500;
    }
}
