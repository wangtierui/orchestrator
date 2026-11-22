package com.orchestrator.crawler.adapter.mof;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.databind.JsonNode;
import lombok.Data;

import java.util.List;

@Data
@JsonIgnoreProperties(ignoreUnknown = true)
public class MofApiResponse {

    private int code;
    private String message;
    private ResponseData data;

    @Data
    @JsonIgnoreProperties(ignoreUnknown = true)
    public static class ResponseData {
        private List<JsonNode> records;
        private String total;
        private String size;
        private String current;
        private String pages;
    }
}
