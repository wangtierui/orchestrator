package com.orchestrator.crawler.model;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableField;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;

import java.time.LocalDateTime;

@Data
@TableName("documents")
public class Document {

    @TableId(type = IdType.AUTO)
    private Long id;

    @TableField("site_id")
    private String siteId;

    @TableField("source_id")
    private String sourceId;

    private String title;

    @TableField("doc_no")
    private String docNo;

    @TableField("issuing_body")
    private String issuingBody;

    @TableField("publish_date")
    private LocalDateTime publishDate;

    @TableField("effect_date")
    private LocalDateTime effectDate;

    @TableField("expire_date")
    private LocalDateTime expireDate;

    private String content;

    private String summary;

    private String keywords;

    @TableField("raw_json")
    private String rawJson;

    @TableField("crawl_status")
    private String crawlStatus;

    @TableField("created_at")
    private LocalDateTime createdAt;

    @TableField("updated_at")
    private LocalDateTime updatedAt;
}
