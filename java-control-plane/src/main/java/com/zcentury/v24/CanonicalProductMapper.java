package com.zcentury.v24;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collection;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** V24.6 dependency-free Java reproduction of the current Python canonical product mapper. */
final class CanonicalProductMapper {
    static final String SCHEMA_VERSION = "canonicalProductSnapshot.v1";

    private static final List<String> CORE_METRICS = List.of(
        "paymentAmount", "roi", "roas", "adSpend", "clickRate", "conversionRate", "refundRate", "inventory"
    );
    private static final List<String> METRIC_FIELDS = List.of(
        "roas", "roi", "adSpend", "paymentAmount", "grossMargin", "clickRate", "conversionRate",
        "refundRate", "inventory", "sellableDays", "organicVisitors", "paidVisitors", "inventoryStatus", "afterSales"
    );
    private static final List<String> PROFILE_FIELDS = List.of(
        "objectId", "productId", "skuId", "spuId", "erpProductCode", "storeId", "storeName", "platform", "title",
        "shortName", "productUrl", "verticalCategory", "categoryLevel1", "categoryLevel2", "categoryLevel3", "priceBand",
        "productRole", "lifecycleStage", "isHeroProduct", "isNewProduct", "isCampaignProduct", "metricDate", "reportDate", "dataDate"
    );
    private static final Set<String> VOLATILE_FACT_FIELDS = Set.of("createdAt", "updatedAt", "created_at", "updated_at");
    private static final Set<String> EMPTY_SENTINELS = Set.of("", "—", "未识别");

    private CanonicalProductMapper() {}

    static Map<String, Object> build(Map<String, Object> source, String dataVersion) {
        Map<String, Object> item = new LinkedHashMap<>(source);
        Map<String, Object> permissionRef = permissionRef(item);
        Map<String, Object> profile = profile(item);
        Map<String, Object> metric = metric(item);
        List<String> factHashRefs = factHashRefs(metric);
        List<String> factIdRefs = factIdRefs(metric);
        List<String> reportRefs = sourceReportRefs(item, metric);
        List<String> contentHashes = sourceContentHashes(item, metric);
        List<String> sourceVersions = unique(orFallbackStrings(metric.get("sourceDataVersions"), dataVersion));
        List<String> sourceDatasets = unique(asValues(metric.get("sourceDatasets")));
        List<String> sourceRefs = sourceRefs(metric, reportRefs);
        Map<String, Object> factContract = productFactContract(item, metric);

        LinkedHashMap<String, Object> base = new LinkedHashMap<>();
        base.put("schemaVersion", SCHEMA_VERSION);
        base.put("dataVersion", dataVersion);
        base.put("objectId", profile.get("objectId"));
        base.put("productId", profile.get("productId"));
        base.put("storeId", profile.get("storeId"));
        base.put("platform", profile.get("platform"));
        base.put("productRole", profile.get("productRole"));
        base.put("category", profile.get("verticalCategory"));
        base.put("priceBand", profile.get("priceBand"));
        base.put("lifecycleStage", profile.get("lifecycleStage"));
        base.put("profileSnapshot", profile);
        base.put("metricSnapshot", metric);
        base.put("trafficSourceFacts", copyList(metric.get("trafficSourceFacts")));
        base.put("productMetricFacts", copyList(metric.get("productMetricFacts")));
        base.put("metricFactSummary", copyMap(metric.get("metricFactSummary")));
        base.put("factRefs", factIdRefs);
        base.put("factHashRefs", factHashRefs);
        base.put("sourceDataVersions", sourceVersions);
        base.put("sourceDataVersion", sourceVersions.isEmpty() ? dataVersion : sourceVersions.get(sourceVersions.size() - 1));
        base.put("sourceDatasets", sourceDatasets);
        base.put("sourceDataset", lastOrNull(sourceDatasets));
        base.put("sourceReportRefs", reportRefs);
        base.put("sourceReportRef", lastOrNull(reportRefs));
        base.put("sourceContentHashes", contentHashes);
        base.put("sourceContentHash", lastOrNull(contentHashes));
        base.put("sourceArtifactRefs", sourceRefs);
        base.put("freshnessStatus", item.get("freshnessStatus"));
        base.put("freshnessAgeDays", item.get("freshnessAgeDays"));
        base.put("freshnessPolicyDays", item.get("freshnessPolicyDays"));
        base.put("metricDate", metric.get("metricDate"));
        base.put("reportDate", metric.get("reportDate"));
        base.put("dataDate", metric.get("dataDate"));
        base.put("dataProvenance", factContract.get("dataProvenance"));
        base.put("dataSourceMode", factContract.get("dataSourceMode"));
        base.put("factContract", factContract);
        base.putAll(permissionRef);
        base.put("permissionRef", permissionRef);
        base.put("permissionRequired", Boolean.TRUE);
        base.put("completeness", completeness(metric));

        String hash = Hashing.canonicalHash(base);
        LinkedHashMap<String, Object> result = new LinkedHashMap<>(base);
        result.put("productSnapshotHash", hash);
        result.put("snapshotHash", hash);
        result.put("parentSnapshotHash", hash);
        return result;
    }

    private static Map<String, Object> profile(Map<String, Object> item) {
        LinkedHashMap<String, Object> out = new LinkedHashMap<>();
        for (String field : PROFILE_FIELDS) out.put(field, item.get(field));
        out.put("objectId", productKey(item));
        out.put("productId", first(item, List.of("productId", "id"), null));
        out.put("skuId", first(item, List.of("skuId", "sku", "sku_id"), null));
        out.put("spuId", first(item, List.of("spuId", "spu", "spu_id"), null));
        out.put("erpProductCode", first(item, List.of("erpProductCode", "erpCode", "erp_product_code", "商家编码"), null));
        out.put("storeName", first(item, List.of("storeName", "store"), null));
        out.put("platform", first(item, List.of("platform", "平台"), "unknown"));
        out.put("title", item.get("title"));
        out.put("shortName", item.get("shortName"));
        out.put("productUrl", first(item, List.of("productUrl", "productLink", "link", "url", "商品链接"), null));
        out.put("categoryLevel1", first(item, List.of("categoryLevel1", "一级类目"), null));
        out.put("categoryLevel2", first(item, List.of("categoryLevel2", "二级类目"), null));
        out.put("categoryLevel3", first(item, List.of("categoryLevel3", "三级类目"), null));
        out.put("verticalCategory", first(item, List.of(
            "verticalCategory", "vertical_category", "category", "categoryName", "categoryLevel3", "categoryLevel2", "categoryLevel1", "二级类目", "一级类目"
        ), "未归类"));
        out.put("priceBand", first(item, List.of("priceBand", "price_band", "价格带"), "unknown"));
        out.put("productRole", first(item, List.of("productRole", "role", "商品角色"), "regular"));
        out.put("lifecycleStage", first(item, List.of("lifecycleStage", "lifecycle", "生命周期"), "unknown"));
        out.put("isHeroProduct", truthy(firstRaw(item, "isHeroProduct", "hero", "主推品")));
        out.put("isNewProduct", truthy(firstRaw(item, "isNewProduct", "new", "新品")));
        out.put("isCampaignProduct", truthy(firstRaw(item, "isCampaignProduct", "campaign", "活动品")));
        out.put("updatedAtFromReport", item.get("updatedAtFromReport"));
        out.putAll(permissionRef(item));
        return out;
    }

    private static Map<String, Object> metric(Map<String, Object> item) {
        LinkedHashMap<String, Object> out = new LinkedHashMap<>();
        out.put("objectId", productKey(item));
        out.put("productId", first(item, List.of("productId", "id"), null));
        out.put("storeId", item.get("storeId"));
        for (String field : METRIC_FIELDS) out.put(field, item.get(field));
        if (!meaningful(out.get("roas"))) out.put("roas", item.get("roi"));
        out.put("metricDate", item.get("metricDate"));
        out.put("reportDate", item.get("reportDate"));
        out.put("dataDate", item.get("dataDate"));
        out.put("updatedAtFromReport", item.get("updatedAtFromReport"));
        out.put("sourceDataVersions", copyList(item.get("sourceDataVersions")));
        out.put("sourceDatasets", copyList(item.get("sourceDatasets")));
        Object metricFacts = item.containsKey("productMetricFacts") ? item.get("productMetricFacts") : item.get("metricFacts");
        out.put("metricFacts", copyList(metricFacts));
        out.put("productMetricFacts", copyList(item.get("productMetricFacts")));
        out.put("trafficSourceFacts", copyList(item.get("trafficSourceFacts")));
        out.put("metricFactSummary", copyMap(item.get("metricFactSummary")));
        out.putAll(permissionRef(item));
        LinkedHashMap<String, Object> namespace = new LinkedHashMap<>();
        namespace.put("productMetricFacts", copyList(out.get("productMetricFacts")).size());
        namespace.put("trafficSourceFacts", copyList(out.get("trafficSourceFacts")).size());
        namespace.put("rule", "product metrics and traffic source metrics are separate namespaces.");
        out.put("factNamespace", namespace);
        return out;
    }

    private static Map<String, Object> permissionRef(Map<String, Object> item) {
        Map<String, Object> stamp = mapOrEmpty(item.get("permissionStamp"));
        Object stampId = meaningful(stamp.get("permissionStampId")) ? stamp.get("permissionStampId") : item.get("permissionStampId");
        Object status = meaningful(item.get("permissionGateStatus")) ? item.get("permissionGateStatus") : (meaningful(stampId) ? "passed" : "quarantine");
        Object scope = meaningful(item.get("permissionScopeRef")) ? item.get("permissionScopeRef") : (meaningful(stampId) ? "permission_stamp:" + stampId : "permission_stamp:missing");
        LinkedHashMap<String, Object> out = new LinkedHashMap<>();
        out.put("permissionStampId", stampId);
        out.put("permissionGateStatus", status);
        out.put("permissionScopeRef", scope);
        return out;
    }

    private static String productKey(Map<String, Object> item) {
        String explicit = text(item.get("objectId"));
        if (!explicit.isBlank()) return explicit;
        return String.join("::",
            defaultText(item.get("platform"), "unknown"),
            defaultText(item.get("storeId"), "GLOBAL"),
            defaultText(first(item, List.of("productId", "id"), null), "PRODUCT"),
            defaultText(item.get("skuId"), "NO-SKU")
        );
    }

    private static List<Map<String, Object>> allFacts(Map<String, Object> metric) {
        List<Map<String, Object>> out = new ArrayList<>();
        for (Object raw : concat(copyList(metric.get("productMetricFacts")), copyList(metric.get("trafficSourceFacts")))) {
            if (raw instanceof Map<?, ?>) out.add(Json.object(raw));
        }
        return out;
    }

    private static String factHash(Map<String, Object> fact) {
        for (String key : List.of("sourceHash", "source_hash", "factHash", "fact_hash", "hash")) {
            if (meaningful(fact.get(key))) return String.valueOf(fact.get(key));
        }
        LinkedHashMap<String, Object> stable = new LinkedHashMap<>();
        for (Map.Entry<String, Object> entry : fact.entrySet()) {
            if (!VOLATILE_FACT_FIELDS.contains(entry.getKey())) stable.put(entry.getKey(), entry.getValue());
        }
        return Hashing.canonicalHash(stable);
    }

    private static List<String> factHashRefs(Map<String, Object> metric) {
        List<Object> values = new ArrayList<>();
        for (Map<String, Object> fact : allFacts(metric)) values.add(factHash(fact));
        return unique(values);
    }

    private static List<String> factIdRefs(Map<String, Object> metric) {
        List<Object> values = new ArrayList<>();
        for (Map<String, Object> fact : allFacts(metric)) values.add(first(fact, List.of("factId", "fact_id", "metricFactId", "metric_fact_id", "id"), null));
        return unique(values);
    }

    private static List<String> sourceReportRefs(Map<String, Object> item, Map<String, Object> metric) {
        List<Object> values = new ArrayList<>(scalarOrList(firstRaw(item, "sourceReportRefs", "source_report_refs")));
        values.add(item.get("sourceReportRef"));
        values.add(item.get("source_report_ref"));
        for (Map<String, Object> fact : allFacts(metric)) {
            values.add(fact.get("sourceReportRef"));
            values.add(fact.get("source_report_ref"));
            values.add(fact.get("sourceArtifactRef"));
            values.add(fact.get("source_artifact_ref"));
        }
        return unique(values);
    }

    private static List<String> sourceContentHashes(Map<String, Object> item, Map<String, Object> metric) {
        List<Object> values = new ArrayList<>(scalarOrList(firstRaw(item, "sourceContentHashes", "source_content_hashes")));
        values.add(item.get("sourceContentHash"));
        values.add(item.get("source_content_hash"));
        for (Map<String, Object> fact : allFacts(metric)) {
            values.add(fact.get("sourceHash"));
            values.add(fact.get("source_hash"));
        }
        return unique(values);
    }

    private static List<String> sourceRefs(Map<String, Object> metric, List<String> reportRefs) {
        List<Object> values = new ArrayList<>();
        for (Object value : copyList(metric.get("sourceDataVersions"))) if (meaningful(value)) values.add("dataVersion:" + value);
        for (Object value : copyList(metric.get("sourceDatasets"))) if (meaningful(value)) values.add("dataset:" + value);
        for (String value : reportRefs) values.add("report:" + value);
        return unique(values);
    }

    private static List<Map<String, Object>> metricLineage(Map<String, Object> metric) {
        List<Map<String, Object>> out = new ArrayList<>();
        for (Map<String, Object> fact : allFacts(metric)) {
            LinkedHashMap<String, Object> row = new LinkedHashMap<>();
            row.put("factId", first(fact, List.of("factId", "fact_id", "metricFactId", "metric_fact_id", "id"), null));
            row.put("factHash", factHash(fact));
            row.put("metricName", first(fact, List.of("metricName", "metric_name", "name"), null));
            row.put("level", first(fact, List.of("level", "scope", "factLevel", "fact_level"), null));
            row.put("sourceRowId", first(fact, List.of("sourceRowId", "source_row_id", "rowId", "row_id"), null));
            row.put("sourceReportRef", first(fact, List.of("sourceReportRef", "source_report_ref", "sourceArtifactRef", "source_artifact_ref"), null));
            out.add(row);
        }
        return out;
    }

    private static Map<String, Object> completeness(Map<String, Object> metric) {
        List<String> present = new ArrayList<>();
        List<String> missing = new ArrayList<>();
        for (String metricName : CORE_METRICS) {
            if (meaningful(metric.get(metricName))) present.add(metricName); else missing.add(metricName);
        }
        LinkedHashMap<String, Object> out = new LinkedHashMap<>();
        out.put("requiredMetricCount", CORE_METRICS.size());
        out.put("presentMetricCount", present.size());
        out.put("missingMetrics", missing);
        out.put("complete", missing.isEmpty());
        return out;
    }

    private static Map<String, Object> productFactContract(Map<String, Object> item, Map<String, Object> metric) {
        List<String> factIds = factIdRefs(metric);
        List<String> versions = unique(asValues(metric.get("sourceDataVersions")));
        List<String> datasets = unique(asValues(metric.get("sourceDatasets")));
        List<String> reports = sourceReportRefs(item, metric);
        List<Object> levelValues = new ArrayList<>();
        for (Map<String, Object> fact : allFacts(metric)) levelValues.add(first(fact, List.of("level", "scope", "factLevel", "fact_level"), null));
        List<String> levels = unique(levelValues);
        LinkedHashMap<String, Object> out = new LinkedHashMap<>();
        out.put("contract", "productSnapshot.factContract.v1");
        out.put("scope", "product");
        out.put("allowedLevels", List.of("product", "traffic_source"));
        out.put("matchedLevels", levels);
        out.put("factRefs", factIds);
        out.put("metricCount", copyList(metric.get("productMetricFacts")).size());
        out.put("groupedMetricCount", copyMap(metric.get("metricFactSummary")).size());
        out.put("trafficSourceFactCount", copyList(metric.get("trafficSourceFacts")).size());
        out.put("usesMetricFactIds", Boolean.TRUE);
        out.put("sourceDataVersion", lastOrNull(versions));
        out.put("sourceDataVersions", versions);
        out.put("sourceDataset", lastOrNull(datasets));
        out.put("sourceDatasets", datasets);
        out.put("sourceReportRef", lastOrNull(reports));
        out.put("sourceReportRefs", reports);
        out.put("dataProvenance", meaningful(item.get("dataProvenance")) ? item.get("dataProvenance") : "materialized_metric_facts");
        out.put("dataSourceMode", meaningful(item.get("dataSourceMode")) ? item.get("dataSourceMode") : "fact_store");
        out.put("metricLineage", metricLineage(metric));
        return out;
    }

    private static Object first(Map<String, Object> item, List<String> keys, Object fallback) {
        for (String key : keys) if (meaningful(item.get(key))) return item.get(key);
        return fallback;
    }

    private static Object firstRaw(Map<String, Object> item, String... keys) {
        for (String key : keys) if (item.get(key) != null) return item.get(key);
        return null;
    }

    private static boolean meaningful(Object value) {
        if (value == null) return false;
        if (value instanceof String text) return !EMPTY_SENTINELS.contains(text);
        return true;
    }

    private static boolean truthy(Object value) {
        if (value == null) return false;
        if (value instanceof Boolean b) return b;
        if (value instanceof Number n) return n.doubleValue() != 0.0d;
        if (value instanceof String text) return !text.isEmpty();
        if (value instanceof Collection<?> collection) return !collection.isEmpty();
        if (value instanceof Map<?, ?> map) return !map.isEmpty();
        return true;
    }

    private static String text(Object value) { return value == null ? "" : String.valueOf(value).trim(); }
    private static String defaultText(Object value, String fallback) { String text = text(value); return text.isBlank() ? fallback : text; }
    private static Object lastOrNull(List<?> values) { return values.isEmpty() ? null : values.get(values.size() - 1); }

    private static List<Object> asValues(Object value) {
        if (value == null) return new ArrayList<>();
        if (value instanceof List<?> list) return new ArrayList<>(list);
        return new ArrayList<>(List.of(value));
    }

    private static List<Object> scalarOrList(Object value) { return asValues(value); }

    private static List<Object> orFallbackStrings(Object value, String fallback) {
        List<Object> values = asValues(value);
        if (!values.isEmpty()) return values;
        if (fallback == null || fallback.isBlank()) return values;
        values.add(fallback);
        return values;
    }

    private static List<String> unique(Iterable<?> values) {
        LinkedHashSet<String> set = new LinkedHashSet<>();
        for (Object value : values) if (meaningful(value)) set.add(String.valueOf(value));
        ArrayList<String> result = new ArrayList<>(set);
        Collections.sort(result);
        return result;
    }

    private static List<Object> copyList(Object value) {
        if (value instanceof List<?> list) return new ArrayList<>(list);
        return new ArrayList<>();
    }

    private static Map<String, Object> mapOrEmpty(Object value) {
        if (value instanceof Map<?, ?>) return new LinkedHashMap<>(Json.object(value));
        return new LinkedHashMap<>();
    }

    private static Map<String, Object> copyMap(Object value) { return mapOrEmpty(value); }

    private static List<Object> concat(List<Object> left, List<Object> right) {
        ArrayList<Object> out = new ArrayList<>(left);
        out.addAll(right);
        return out;
    }
}
