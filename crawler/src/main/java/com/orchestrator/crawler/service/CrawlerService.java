package com.orchestrator.crawler.service;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.orchestrator.crawler.adapter.AdapterRegistry;
import com.orchestrator.crawler.adapter.DocumentData;
import com.orchestrator.crawler.adapter.PageResult;
import com.orchestrator.crawler.adapter.SiteAdapter;
import com.orchestrator.crawler.config.SiteProperties;
import com.orchestrator.crawler.mapper.CheckpointMapper;
import com.orchestrator.crawler.mapper.DocumentMapper;
import com.orchestrator.crawler.model.CrawlCheckpoint;
import com.orchestrator.crawler.model.Document;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;

import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

@Slf4j
@Service
@RequiredArgsConstructor
public class CrawlerService {

    private static final DateTimeFormatter DF = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss");

    private final AdapterRegistry adapterRegistry;
    private final DocumentMapper documentMapper;
    private final CheckpointMapper checkpointMapper;
    private final SiteProperties siteProperties;

    public CrawlResult crawl(String siteId, boolean full) {
        SiteAdapter adapter = adapterRegistry.get(siteId);
        SiteProperties.SiteConfig config = siteProperties.get(siteId);
        CrawlCheckpoint checkpoint = checkpointMapper.selectById(siteId);

        int size = config.getPageSize();
        int maxPages = config.getMaxPages();
        int page = 1;
        int newCount = 0;
        int duplicateCount = 0;
        boolean reachedCheckpoint = false;
        String latestDocId = null;
        LocalDateTime latestPublishDate = null;

        log.info("Crawl start site={} full={} checkpoint={}", siteId, full,
                checkpoint == null ? "none" : checkpoint.getLastDocId());

        while (page <= maxPages) {
            PageResult result = adapter.fetchPage(page, size);
            List<DocumentData> records = result.records();
            if (records.isEmpty()) {
                log.info("Empty page, stop. site={} page={}", siteId, page);
                break;
            }

            Set<String> pageIds = records.stream().map(DocumentData::sourceId).collect(java.util.stream.Collectors.toSet());
            Set<String> existingIds = findExistingIds(siteId, pageIds);

            for (DocumentData data : records) {
                if (existingIds.contains(data.sourceId())) {
                    duplicateCount++;
                    if (!full) {
                        log.info("Reached existing doc, stop. site={} id={} title={}",
                                siteId, data.sourceId(), data.title());
                        reachedCheckpoint = true;
                        break;
                    }
                    continue;
                }
                documentMapper.insert(toEntity(siteId, data));
                newCount++;
                if (latestDocId == null) {
                    latestDocId = data.sourceId();
                    latestPublishDate = parseDate(data.publishDate());
                }
            }

            if (reachedCheckpoint) break;
            if (page >= result.pages()) break;

            page++;
            sleep(config.getIntervalMs());
        }

        if (latestDocId != null) {
            updateCheckpoint(siteId, newCount, latestDocId, latestPublishDate);
        }
        CrawlResult cr = new CrawlResult(siteId, full, newCount, duplicateCount, page);
        log.info("Crawl done {}", cr);
        return cr;
    }

    private Set<String> findExistingIds(String siteId, Set<String> ids) {
        if (ids.isEmpty()) return new HashSet<>();
        LambdaQueryWrapper<Document> q = new LambdaQueryWrapper<>();
        q.select(Document::getSourceId)
                .eq(Document::getSiteId, siteId)
                .in(Document::getSourceId, ids);
        Set<String> found = new HashSet<>();
        for (Document d : documentMapper.selectList(q)) {
            found.add(d.getSourceId());
        }
        return found;
    }

    private Document toEntity(String siteId, DocumentData data) {
        Document doc = new Document();
        doc.setSiteId(siteId);
        doc.setSourceId(data.sourceId());
        doc.setTitle(data.title());
        doc.setDocNo(data.docNo());
        doc.setIssuingBody(data.issuingBody());
        doc.setPublishDate(parseDate(data.publishDate()));
        doc.setEffectDate(parseDate(data.effectDate()));
        doc.setExpireDate(parseDate(data.expireDate()));
        doc.setContent(data.content());
        doc.setSummary(data.summary());
        doc.setKeywords(data.keywords());
        doc.setRawJson(data.rawJson());
        doc.setCrawlStatus("pending");
        LocalDateTime now = LocalDateTime.now();
        doc.setCreatedAt(now);
        doc.setUpdatedAt(now);
        return doc;
    }

    private LocalDateTime parseDate(String s) {
        if (s == null || s.isBlank()) return null;
        try {
            return LocalDateTime.parse(s.trim(), DF);
        } catch (Exception e) {
            return null;
        }
    }

    private void updateCheckpoint(String siteId, int newCount, String lastDocId, LocalDateTime lastPublishDate) {
        CrawlCheckpoint cp = checkpointMapper.selectById(siteId);
        boolean exists = cp != null;
        LocalDateTime now = LocalDateTime.now();
        if (!exists) {
            cp = new CrawlCheckpoint();
            cp.setSiteId(siteId);
            cp.setTotalCrawled(newCount);
        } else {
            cp.setTotalCrawled((cp.getTotalCrawled() == null ? 0 : cp.getTotalCrawled()) + newCount);
        }
        cp.setLastDocId(lastDocId);
        cp.setLastDocPublishDate(lastPublishDate);
        cp.setLastCrawlAt(now);
        cp.setStatus("idle");
        if (exists) {
            checkpointMapper.updateById(cp);
        } else {
            checkpointMapper.insert(cp);
        }
    }

    private void sleep(long ms) {
        if (ms <= 0) return;
        try {
            Thread.sleep(ms);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }

    public record CrawlResult(String siteId, boolean full, int newCount, int duplicateCount, int pagesFetched) {}
}
