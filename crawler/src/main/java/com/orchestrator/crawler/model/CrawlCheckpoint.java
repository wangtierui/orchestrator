package com.orchestrator.crawler.model;

import com.baomidou.mybatisplus.annotation.TableField;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;

import java.time.LocalDateTime;

@Data
@TableName("crawl_checkpoint")
public class CrawlCheckpoint {

    @TableId
    @TableField("site_id")
    private String siteId;

    @TableField("last_doc_id")
    private String lastDocId;

    @TableField("last_doc_publish_date")
    private LocalDateTime lastDocPublishDate;

    @TableField("last_crawl_at")
    private LocalDateTime lastCrawlAt;

    private String status;

    @TableField("total_crawled")
    private Integer totalCrawled;
}
