package com.orchestrator.crawler.client;

import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import okhttp3.*;
import org.springframework.stereotype.Component;

import java.io.IOException;
import java.util.Map;

@Slf4j
@Component
@RequiredArgsConstructor
public class ApiClient {

    private static final MediaType JSON = MediaType.get("application/json;charset=UTF-8");
    private static final String UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            + "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36";

    private final OkHttpClient httpClient;
    private final ObjectMapper objectMapper;

    public String postJson(String url, Map<String, Object> body) {
        String json;
        try {
            json = objectMapper.writeValueAsString(body);
        } catch (Exception e) {
            throw new RuntimeException("Failed to serialize request body", e);
        }

        RequestBody requestBody = RequestBody.create(json, JSON);
        Request request = new Request.Builder()
                .url(url)
                .header("User-Agent", UA)
                .post(requestBody)
                .build();

        IOException lastError = null;
        for (int attempt = 1; attempt <= 3; attempt++) {
            try (Response response = httpClient.newCall(request).execute()) {
                if (!response.isSuccessful()) {
                    throw new IOException("HTTP " + response.code());
                }
                return response.body().string();
            } catch (IOException e) {
                lastError = e;
                log.warn("Request failed (attempt {}/3) url={}: {}", attempt, url, e.getMessage());
                if (attempt < 3) {
                    sleep(1000L * attempt);
                }
            }
        }
        throw new RuntimeException("Request failed after 3 attempts: " + url, lastError);
    }

    private void sleep(long ms) {
        try {
            Thread.sleep(ms);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }
}
