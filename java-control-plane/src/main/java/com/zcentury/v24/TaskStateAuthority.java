package com.zcentury.v24;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** V24.8 shadow task-state authority with fail-closed state and optimistic-version decisions. */
final class TaskStateAuthority {
    private static final Map<String, Set<String>> ALLOWED = buildAllowed();
    private static final Set<String> DONE = Set.of("已完成", "已拒绝", "已确认", "已归档", "已通过", "已写入复盘");
    private static final Map<String, String> ACTION_TARGET = buildActionTargets();

    private TaskStateAuthority() {}

    static Map<String, Object> decide(String fromStatus, String toStatus, long currentVersion, long expectedVersion) {
        LinkedHashMap<String, Object> material = new LinkedHashMap<>();
        material.put("fromStatus", fromStatus);
        material.put("toStatus", toStatus);
        material.put("currentVersion", currentVersion);
        material.put("expectedVersion", expectedVersion);

        String decision;
        String reason;
        if (currentVersion != expectedVersion) {
            decision = "CONFLICT";
            reason = "STATE_VERSION_MISMATCH";
        } else if (!knownState(fromStatus) || !knownState(toStatus)) {
            decision = "BLOCK";
            reason = "UNKNOWN_STATE";
        } else if (fromStatus.equals(toStatus)) {
            decision = "PASS";
            reason = "IDEMPOTENT_SAME_STATE";
        } else if (ALLOWED.getOrDefault(fromStatus, Set.of()).contains(toStatus)) {
            decision = "PASS";
            reason = "ALLOWED_TRANSITION";
        } else if (DONE.contains(fromStatus)) {
            decision = "BLOCK";
            reason = "TERMINAL_REOPEN_BLOCKED";
        } else {
            decision = "BLOCK";
            reason = "ILLEGAL_TRANSITION";
        }
        material.put("decision", decision);
        material.put("reason", reason);
        material.put("transitionAllowed", "PASS".equals(decision));
        material.put("versionMatch", currentVersion == expectedVersion);
        if ("PASS".equals(decision)) material.put("nextVersion", currentVersion + (fromStatus.equals(toStatus) ? 0 : 1));
        else material.put("nextVersion", currentVersion);
        String hash = Hashing.canonicalHash(material);
        LinkedHashMap<String, Object> result = new LinkedHashMap<>(material);
        result.put("decisionHash", hash);
        return result;
    }

    static void assertPythonMatrix(Map<String, Object> taskStateEvidence) {
        Map<String, Object> pythonAllowed = Json.object(taskStateEvidence.get("allowedTransitions"));
        if (pythonAllowed.size() != ALLOWED.size()) throw new IllegalStateException("task_transition_state_count_mismatch");
        for (Map.Entry<String, Set<String>> entry : ALLOWED.entrySet()) {
            Object raw = pythonAllowed.get(entry.getKey());
            if (!(raw instanceof List<?> list)) throw new IllegalStateException("python_transition_state_missing:" + entry.getKey());
            LinkedHashSet<String> actual = new LinkedHashSet<>();
            for (Object item : list) actual.add(String.valueOf(item));
            if (!actual.equals(entry.getValue())) throw new IllegalStateException("python_transition_mismatch:" + entry.getKey() + ":" + actual + ":" + entry.getValue());
        }
        Set<String> pythonDone = new LinkedHashSet<>();
        for (Object item : Json.array(taskStateEvidence.get("doneStatuses"))) pythonDone.add(String.valueOf(item));
        if (!pythonDone.equals(DONE)) throw new IllegalStateException("python_done_status_mismatch:" + pythonDone + ":" + DONE);

        Map<String, Object> pythonActions = Json.object(taskStateEvidence.get("actionTargetStatus"));
        if (pythonActions.size() != ACTION_TARGET.size()) throw new IllegalStateException("task_action_target_count_mismatch");
        for (Map.Entry<String, String> entry : ACTION_TARGET.entrySet()) {
            Object actual = pythonActions.get(entry.getKey());
            if (entry.getValue() == null ? actual != null : !entry.getValue().equals(String.valueOf(actual))) {
                throw new IllegalStateException("task_action_target_mismatch:" + entry.getKey());
            }
        }
    }

    static Map<String, Set<String>> allowedTransitions() { return ALLOWED; }

    private static boolean knownState(String status) {
        if (status == null || status.isBlank()) return false;
        if (ALLOWED.containsKey(status) || DONE.contains(status)) return true;
        for (Set<String> targets : ALLOWED.values()) if (targets.contains(status)) return true;
        return false;
    }

    private static Map<String, Set<String>> buildAllowed() {
        LinkedHashMap<String, Set<String>> map = new LinkedHashMap<>();
        map.put("待拆分", ordered("待接收", "已归档"));
        map.put("待接收", ordered("处理中", "待接收", "已归档"));
        map.put("待确认", ordered("处理中", "待接收", "已归档"));
        map.put("已派发", ordered("处理中", "待接收", "已归档"));
        map.put("处理中", ordered("待复核", "已完成", "已退回", "已归档"));
        map.put("已退回", ordered("处理中", "待复核", "已归档"));
        map.put("待复核", ordered("已完成", "已退回", "已归档"));
        map.put("已提交", ordered("待复核", "已完成", "已退回", "已归档"));
        map.put("已完成", ordered("已写入复盘", "已归档"));
        map.put("复核通过", ordered("已写入复盘", "已归档"));
        map.put("已通过", ordered("已写入复盘", "已归档"));
        map.put("已写入复盘", ordered("已归档"));
        map.put("已归档", ordered());
        return Map.copyOf(map);
    }

    private static Map<String, String> buildActionTargets() {
        LinkedHashMap<String, String> map = new LinkedHashMap<>();
        map.put("task_created", null);
        map.put("task_merged", null);
        map.put("manager_assigned", "待接收");
        map.put("manager_split", "待接收");
        map.put("operator_accepted", "处理中");
        map.put("operator_submitted", "待复核");
        map.put("manager_returned", "已退回");
        map.put("manager_approved", "已完成");
        map.put("task_completed", "已完成");
        map.put("task_written_to_recap", "已写入复盘");
        map.put("task_pinned", null);
        map.put("task_reordered", null);
        map.put("demo_reset", null);
        map.put("数据版本回滚", "待复核");
        return map;
    }

    private static Set<String> ordered(String... values) {
        LinkedHashSet<String> set = new LinkedHashSet<>();
        for (String value : values) set.add(value);
        return set;
    }
}
