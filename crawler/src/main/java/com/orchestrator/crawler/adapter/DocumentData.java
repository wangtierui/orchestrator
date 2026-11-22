package com.orchestrator.crawler.adapter;

public record DocumentData(
        String sourceId,
        String title,
        String docNo,
        String publishDate,
        String effectDate,
        String expireDate,
        String content,
        String summary,
        String issuingBody,
        String keywords,
        String rawJson
) {}
