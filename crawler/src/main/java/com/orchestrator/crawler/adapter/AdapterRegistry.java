package com.orchestrator.crawler.adapter;

import org.springframework.stereotype.Component;

import java.util.List;
import java.util.Map;
import java.util.function.Function;
import java.util.stream.Collectors;

@Component
public class AdapterRegistry {

    private final Map<String, SiteAdapter> adapters;

    public AdapterRegistry(List<SiteAdapter> adapterList) {
        this.adapters = adapterList.stream()
                .collect(Collectors.toMap(SiteAdapter::siteId, Function.identity()));
    }

    public SiteAdapter get(String siteId) {
        SiteAdapter adapter = adapters.get(siteId);
        if (adapter == null) {
            throw new IllegalArgumentException("Unknown site: " + siteId + ", available: " + adapters.keySet());
        }
        return adapter;
    }
}
