package com.orchestrator.crawler.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.orchestrator.crawler.model.CrawlCheckpoint;
import org.apache.ibatis.annotations.Mapper;

@Mapper
public interface CheckpointMapper extends BaseMapper<CrawlCheckpoint> {
}
