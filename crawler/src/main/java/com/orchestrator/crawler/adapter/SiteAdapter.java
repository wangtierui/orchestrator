package com.orchestrator.crawler.adapter;

public interface SiteAdapter {

    String siteId();

    PageResult fetchPage(int page, int size);
}
