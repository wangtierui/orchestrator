package com.orchestrator.crawler.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.orchestrator.crawler.model.Document;
import org.apache.ibatis.annotations.Mapper;

@Mapper
public interface DocumentMapper extends BaseMapper<Document> {
}
