package com.orchestrator.crawler.adapter.mof;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.orchestrator.crawler.adapter.DocumentData;
import com.orchestrator.crawler.adapter.PageResult;
import com.orchestrator.crawler.adapter.SiteAdapter;
import com.orchestrator.crawler.client.ApiClient;
import com.orchestrator.crawler.config.SiteProperties;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;

import java.util.List;
import java.util.Map;

@Slf4j
@Component
@RequiredArgsConstructor
public class MofSiteAdapter implements SiteAdapter {

    public static final String SITE_ID = "mof-fgk";

    private final ApiClient apiClient;
    private final SiteProperties siteProperties;
    private final ObjectMapper objectMapper;

    @Override
    public String siteId() {
        return SITE_ID;
    }

    @Override
    public PageResult fetchPage(int page, int size) {
        String endpoint = siteProperties.get(SITE_ID).getEndpoint();
        Map<String, Object> body = Map.of(
                "current", page,
                "size", size,
                "descs", List.of("lBuDate")
        );

        String json = apiClient.postJson(endpoint, body);
        MofApiResponse response;
        try {
            response = objectMapper.readValue(json, MofApiResponse.class);
        } catch (JsonProcessingException e) {
            throw new RuntimeException("Failed to parse MOF API response", e);
        }

        if (response.getCode() != 200 || response.getData() == null) {
            throw new RuntimeException("MOF API error: code=" + response.getCode() + ", msg=" + response.getMessage());
        }

        MofApiResponse.ResponseData data = response.getData();
        List<DocumentData> records = data.getRecords().stream()
                .map(this::toDocumentData)
                .toList();

        return new PageResult(
                records,
                Integer.parseInt(data.getTotal()),
                Integer.parseInt(data.getPages())
        );
    }

    private DocumentData toDocumentData(JsonNode n) {
        String rawJson = n.toString();
        return new DocumentData(
                text(n, "id"),
                text(n, "title"),
                text(n, "lwh"),
                text(n, "lBuDate"),
                text(n, "lSsDate"),
                text(n, "expireDate"),
                stripStyles(text(n, "lcontent")),
                text(n, "lcaption"),
                text(n, "lbbdw"),
                text(n, "lkeyword"),
                rawJson
        );
    }

    private String text(JsonNode n, String field) {
        JsonNode v = n.get(field);
        return (v == null || v.isNull()) ? null : v.asText();
    }

    private String stripStyles(String html) {
        if (html == null) return null;
        return html.replaceAll("(?is)<style[^>]*>.*?</style>", "");
    }
}
