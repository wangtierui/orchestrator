package com.orchestrator.crawler.adapter;

import java.util.List;

public record PageResult(
        List<DocumentData> records,
        int total,
        int pages
) {}
