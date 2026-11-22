package com.orchestrator.crawler.controller;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.orchestrator.crawler.mapper.CheckpointMapper;
import com.orchestrator.crawler.mapper.DocumentMapper;
import com.orchestrator.crawler.model.CrawlCheckpoint;
import com.orchestrator.crawler.model.Document;
import com.orchestrator.crawler.service.CrawlerService;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.*;

import java.util.List;

@RestController
@RequestMapping("/api/crawl")
@RequiredArgsConstructor
public class CrawlController {

    private final CrawlerService crawlerService;
    private final DocumentMapper documentMapper;
    private final CheckpointMapper checkpointMapper;

    @PostMapping("/{siteId}")
    public CrawlerService.CrawlResult trigger(
            @PathVariable String siteId,
            @RequestParam(defaultValue = "false") boolean full) {
        return crawlerService.crawl(siteId, full);
    }

    @GetMapping("/status/{siteId}")
    public CrawlCheckpoint status(@PathVariable String siteId) {
        return checkpointMapper.selectById(siteId);
    }

    @GetMapping("/documents/{siteId}")
    public List<Document> recentDocuments(
            @PathVariable String siteId,
            @RequestParam(defaultValue = "5") int limit) {
        LambdaQueryWrapper<Document> q = new LambdaQueryWrapper<>();
        q.eq(Document::getSiteId, siteId).orderByDesc(Document::getPublishDate).last("LIMIT " + limit);
        return documentMapper.selectList(q);
    }
}
