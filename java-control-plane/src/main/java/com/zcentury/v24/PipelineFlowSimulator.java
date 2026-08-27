package com.zcentury.v24;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Deterministic event simulation proving stage-local capacity and cross-stage overlap. */
final class PipelineFlowSimulator {
    record Interval(String itemId, QueueAuthority.Stage stage, long start, long end, int lane) {}

    private final int agent1Capacity;
    private final int agent2Capacity;
    private final int agent3Capacity;
    private final long agent1Duration;
    private final long agent2Duration;
    private final long agent3Duration;

    PipelineFlowSimulator(
        int agent1Capacity,
        int agent2Capacity,
        int agent3Capacity,
        long agent1Duration,
        long agent2Duration,
        long agent3Duration
    ) {
        this.agent1Capacity = positive(agent1Capacity);
        this.agent2Capacity = positive(agent2Capacity);
        this.agent3Capacity = positive(agent3Capacity);
        this.agent1Duration = positive(agent1Duration);
        this.agent2Duration = positive(agent2Duration);
        this.agent3Duration = positive(agent3Duration);
    }

    Map<String, Object> simulate(int itemCount) {
        if (itemCount <= 0) throw new IllegalArgumentException("item_count_must_be_positive");
        long[] a1 = new long[agent1Capacity];
        long[] a2 = new long[agent2Capacity];
        long[] a3 = new long[agent3Capacity];
        ArrayList<Interval> intervals = new ArrayList<>();

        for (int index = 0; index < itemCount; index++) {
            String itemId = String.format("ITEM-%03d", index + 1);
            Interval i1 = schedule(itemId, QueueAuthority.Stage.AGENT1, 0L, agent1Duration, a1);
            intervals.add(i1);
            Interval i2 = schedule(itemId, QueueAuthority.Stage.AGENT2, i1.end(), agent2Duration, a2);
            intervals.add(i2);
            Interval i3 = schedule(itemId, QueueAuthority.Stage.AGENT3, i2.end(), agent3Duration, a3);
            intervals.add(i3);
        }

        long firstA2 = firstStart(intervals, QueueAuthority.Stage.AGENT2);
        long lastA1 = lastEnd(intervals, QueueAuthority.Stage.AGENT1);
        long firstA3 = firstStart(intervals, QueueAuthority.Stage.AGENT3);
        long lastA2 = lastEnd(intervals, QueueAuthority.Stage.AGENT2);
        long finish = lastEnd(intervals, QueueAuthority.Stage.AGENT3);

        LinkedHashMap<String, Object> result = new LinkedHashMap<>();
        result.put("itemCount", itemCount);
        result.put("agent1Capacity", agent1Capacity);
        result.put("agent2Capacity", agent2Capacity);
        result.put("agent3Capacity", agent3Capacity);
        result.put("agent1Finish", lastA1);
        result.put("agent2FirstStart", firstA2);
        result.put("agent2Finish", lastA2);
        result.put("agent3FirstStart", firstA3);
        result.put("pipelineFinish", finish);
        result.put("agent1Agent2Overlap", firstA2 < lastA1);
        result.put("agent2Agent3Overlap", firstA3 < lastA2);
        result.put("crossStagePipelineOverlap", firstA2 < lastA1 && firstA3 < lastA2);
        result.put("maxConcurrentStages", maxConcurrentStages(intervals));
        result.put("intervalCount", intervals.size());
        return result;
    }

    private static Interval schedule(
        String itemId,
        QueueAuthority.Stage stage,
        long readyAt,
        long duration,
        long[] lanes
    ) {
        int lane = earliestLane(lanes);
        long start = Math.max(readyAt, lanes[lane]);
        long end = start + duration;
        lanes[lane] = end;
        return new Interval(itemId, stage, start, end, lane);
    }

    private static int earliestLane(long[] lanes) {
        int selected = 0;
        for (int i = 1; i < lanes.length; i++) {
            if (lanes[i] < lanes[selected]) selected = i;
        }
        return selected;
    }

    private static long firstStart(List<Interval> intervals, QueueAuthority.Stage stage) {
        return intervals.stream()
            .filter(value -> value.stage() == stage)
            .mapToLong(Interval::start)
            .min()
            .orElse(Long.MAX_VALUE);
    }

    private static long lastEnd(List<Interval> intervals, QueueAuthority.Stage stage) {
        return intervals.stream()
            .filter(value -> value.stage() == stage)
            .mapToLong(Interval::end)
            .max()
            .orElse(0L);
    }

    private static int maxConcurrentStages(List<Interval> intervals) {
        ArrayList<Long> points = new ArrayList<>();
        for (Interval interval : intervals) {
            points.add(interval.start());
            points.add(interval.end());
        }
        points.sort(Long::compareTo);
        int max = 0;
        for (long point : points) {
            int stages = 0;
            for (QueueAuthority.Stage stage : List.of(
                QueueAuthority.Stage.AGENT1,
                QueueAuthority.Stage.AGENT2,
                QueueAuthority.Stage.AGENT3
            )) {
                boolean active = intervals.stream().anyMatch(value ->
                    value.stage() == stage && value.start() <= point && point < value.end()
                );
                if (active) stages++;
            }
            max = Math.max(max, stages);
        }
        return max;
    }

    private static int positive(int value) {
        if (value <= 0) throw new IllegalArgumentException("capacity_must_be_positive");
        return value;
    }

    private static long positive(long value) {
        if (value <= 0) throw new IllegalArgumentException("duration_must_be_positive");
        return value;
    }
}
