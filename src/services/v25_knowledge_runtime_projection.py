"""Generated V25 knowledge runtime projection.

This is the production-readable projection of governance/v25 knowledge contracts.
Governance files remain release/control-plane inputs and are deliberately not a
runtime filesystem dependency. CI re-projects the governance originals and requires
exact equality with these constants before release.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict

V25_KNOWLEDGE_RUNTIME_PROJECTION_VERSION = "25.9.0"

_FIELD_PROJECTION: Dict[str, Any] = {
    "schema": "rag.unified_field_runtime_projection.v1",
    "version": V25_KNOWLEDGE_RUNTIME_PROJECTION_VERSION,
    "sourceSchema": "rag.unified_field_registry.v1",
    "sourceVersion": "25.1.0",
    "defaultDecision": "BLOCK",
    "systemContractExclusions": [
        "permission.*",
        "state.*",
        "schema.*",
        "execution_lock.*",
        "gate.*",
        "runtime.*",
        "deployment.*",
    ],
    "fields": [
        {
            "canonicalField": "metric.ctr.interpretation",
            "fieldHash": "sha256:67d91d83d475bc7dce4d8258f569d0363ac7fbe613b439743a52ce1784b87456",
            "domains": ["rag-domain-operating-diagnosis"],
            "consumers": ["Agent1"],
            "preferredRetrieval": ["EXACT_FIELD", "STRUCTURED_FILTER", "ALIAS", "VECTOR", "GRAPH"],
        },
        {
            "canonicalField": "metric.ctr.related_metrics",
            "fieldHash": "sha256:73945dbbe8b0fe36b4f6c6cf9491d467f082e7f00d895b72825b3292fd58be6e",
            "domains": ["rag-domain-metric-relation"],
            "consumers": ["Agent1"],
            "preferredRetrieval": ["EXACT_FIELD", "STRUCTURED_FILTER", "ALIAS", "VECTOR", "GRAPH"],
        },
        {
            "canonicalField": "traffic.organic.decline_causes",
            "fieldHash": "sha256:68f03c148c4653174aaecf5c9a7647f2a284d61b3db48ae6e95a489afb218547",
            "domains": ["rag-domain-operating-diagnosis"],
            "consumers": ["Agent1"],
            "preferredRetrieval": ["EXACT_FIELD", "STRUCTURED_FILTER", "ALIAS", "VECTOR", "GRAPH"],
        },
        {
            "canonicalField": "diagnosis.cross_metric.patterns",
            "fieldHash": "sha256:806c448b743bde54c9dbbe19880bf2320bbf570b5e242d4e6ef46375e8bab6db",
            "domains": ["rag-domain-operating-diagnosis", "rag-domain-metric-relation"],
            "consumers": ["Agent1"],
            "preferredRetrieval": ["EXACT_FIELD", "STRUCTURED_FILTER", "VECTOR", "GRAPH"],
        },
        {
            "canonicalField": "lifecycle.product.context",
            "fieldHash": "sha256:92f22a3a7c869032e55e5f61e8e4e370194b4653e3996ddd33431e791ecdad37",
            "domains": ["rag-domain-operating-diagnosis"],
            "consumers": ["Agent1"],
            "preferredRetrieval": ["EXACT_FIELD", "STRUCTURED_FILTER", "ALIAS", "VECTOR"],
        },
        {
            "canonicalField": "platform.operation.context",
            "fieldHash": "sha256:9bafe89a2ae1ded2af9406aeaef9a5ca3ae8f2b7976d71c6daa87bbebed262e1",
            "domains": ["rag-domain-platform-operation"],
            "consumers": ["Agent1", "Agent2"],
            "preferredRetrieval": ["EXACT_FIELD", "STRUCTURED_FILTER", "ALIAS", "VECTOR"],
        },
        {
            "canonicalField": "category.operation.context",
            "fieldHash": "sha256:d0a34b0cd704be8147279b27a7d05631a5cb8b9166d5f0ef649c74da5c837311",
            "domains": ["rag-domain-category-operation"],
            "consumers": ["Agent1", "Agent2"],
            "preferredRetrieval": ["EXACT_FIELD", "STRUCTURED_FILTER", "ALIAS", "VECTOR"],
        },
        {
            "canonicalField": "action.title_image.strategy",
            "fieldHash": "sha256:385ddd20be55f62b3322127f68757dac0edbdfed5fb5be2d429d60036bb1e95b",
            "domains": ["rag-domain-creative-operation"],
            "consumers": ["Agent2"],
            "preferredRetrieval": ["EXACT_FIELD", "STRUCTURED_FILTER", "ALIAS", "VECTOR"],
        },
        {
            "canonicalField": "action.roas.scale.strategy",
            "fieldHash": "sha256:94dba96fbc9ea00b1945a4b5b1fe8e22c007460da9ccf40cc88edf5ea940aead",
            "domains": ["rag-domain-paid-operation"],
            "consumers": ["Agent2"],
            "preferredRetrieval": ["EXACT_FIELD", "STRUCTURED_FILTER", "ALIAS", "VECTOR"],
        },
        {
            "canonicalField": "action.roas.guard.strategy",
            "fieldHash": "sha256:82b117f6ddb3271d4e265c43f733940a07cc4534ff45726a2224d3cb67d3b60d",
            "domains": ["rag-domain-paid-operation"],
            "consumers": ["Agent2"],
            "preferredRetrieval": ["EXACT_FIELD", "STRUCTURED_FILTER", "ALIAS", "VECTOR"],
        },
        {
            "canonicalField": "action.platform_activity.strategy",
            "fieldHash": "sha256:bd3575f9c9f816439044dc8441bcf99f14c8246516954ade9c9a785d739fbf99",
            "domains": ["rag-domain-activity-operation"],
            "consumers": ["Agent2"],
            "preferredRetrieval": ["EXACT_FIELD", "STRUCTURED_FILTER", "ALIAS", "VECTOR"],
        },
        {
            "canonicalField": "action.conversion_repair.strategy",
            "fieldHash": "sha256:0079779e79c64c07aaaf907f19778974775cb76bbe8e87bdda60684a5881b0d6",
            "domains": ["rag-domain-conversion-operation"],
            "consumers": ["Agent2"],
            "preferredRetrieval": ["EXACT_FIELD", "STRUCTURED_FILTER", "ALIAS", "VECTOR"],
        },
        {
            "canonicalField": "experience.positive.applicability",
            "fieldHash": "sha256:2f36f52c14daa6705f45b811c95bee80e473bd8d32d6f3b0da1cba17b12d05da",
            "domains": ["rag-domain-execution-experience"],
            "consumers": ["Agent2", "Agent3"],
            "preferredRetrieval": ["EXACT_FIELD", "STRUCTURED_FILTER", "ALIAS", "VECTOR", "GRAPH"],
        },
        {
            "canonicalField": "experience.negative.risk",
            "fieldHash": "sha256:74577c3c8560ce09dab80fd4c0a58e7912bfc883cd08d366b0fa9ea58f0b4425",
            "domains": ["rag-domain-execution-experience"],
            "consumers": ["Agent2", "Agent3"],
            "preferredRetrieval": ["EXACT_FIELD", "STRUCTURED_FILTER", "ALIAS", "VECTOR", "GRAPH"],
        },
        {
            "canonicalField": "company.sop.execution_principles",
            "fieldHash": "sha256:6c337ecb041c552e350f8545aee6c3613e2d050f517106d8f965cb1b5ec441a4",
            "domains": ["rag-domain-company-sop"],
            "consumers": ["Agent3"],
            "preferredRetrieval": ["EXACT_FIELD", "STRUCTURED_FILTER", "ALIAS", "VECTOR"],
        },
        {
            "canonicalField": "company.sop.task_timing",
            "fieldHash": "sha256:f2d30c98af32a63053ebb1be499fd6b88e952710f8085652bce69abbc11ccbc8",
            "domains": ["rag-domain-company-sop"],
            "consumers": ["Agent3"],
            "preferredRetrieval": ["EXACT_FIELD", "STRUCTURED_FILTER"],
        },
        {
            "canonicalField": "brand.expression.style",
            "fieldHash": "sha256:375f4ed437e8765424c5157582358add401019a3d2ee84b9e5a59d874e734787",
            "domains": ["rag-domain-brand-expression"],
            "consumers": ["Agent3"],
            "preferredRetrieval": ["EXACT_FIELD", "STRUCTURED_FILTER", "ALIAS", "VECTOR"],
        },
        {
            "canonicalField": "company.sop.historical_cases",
            "fieldHash": "sha256:23c9344213ab934cfe644130342660b828b5a053fe134799da215cedc1ed30af",
            "domains": ["rag-domain-company-sop", "rag-domain-execution-experience"],
            "consumers": ["Agent3"],
            "preferredRetrieval": ["EXACT_FIELD", "STRUCTURED_FILTER", "ALIAS", "VECTOR", "GRAPH"],
        },
    ],
}

_COMPOSITION_PROJECTION: Dict[str, Any] = {
    "schema": "rag.knowledge_composition_runtime_projection.v1",
    "version": "25.6.0",
    "runtimeProjectionVersion": V25_KNOWLEDGE_RUNTIME_PROJECTION_VERSION,
    "sourceSchema": "rag.knowledge_composition_table.v1",
    "defaultDecision": "BLOCK",
    "allowedPredicateOps": ["EQ", "IN", "CONTAINS", "TRUTHY"],
    "systemContractExclusions": [
        "permission.*",
        "state.*",
        "schema.*",
        "execution_lock.*",
        "gate.*",
        "runtime.*",
        "deployment.*",
    ],
    "compositions": [
        {
            "compositionId": "KCT-AGENT1-DIAGNOSIS-V1",
            "agent": "Agent1",
            "stage": "agent1_diagnosis",
            "baseFields": [
                {"canonicalField": "platform.operation.context", "fieldHash": "sha256:9bafe89a2ae1ded2af9406aeaef9a5ca3ae8f2b7976d71c6daa87bbebed262e1", "role": "REQUIRED"},
                {"canonicalField": "category.operation.context", "fieldHash": "sha256:d0a34b0cd704be8147279b27a7d05631a5cb8b9166d5f0ef649c74da5c837311", "role": "REQUIRED"},
                {"canonicalField": "lifecycle.product.context", "fieldHash": "sha256:92f22a3a7c869032e55e5f61e8e4e370194b4653e3996ddd33431e791ecdad37", "role": "REQUIRED"},
            ],
            "conditionalGroups": [
                {
                    "groupId": "A1-CTR",
                    "when": {"path": "metricCodes", "op": "CONTAINS", "value": "ctr"},
                    "fields": [
                        {"canonicalField": "metric.ctr.interpretation", "fieldHash": "sha256:67d91d83d475bc7dce4d8258f569d0363ac7fbe613b439743a52ce1784b87456", "role": "REQUIRED"},
                        {"canonicalField": "metric.ctr.related_metrics", "fieldHash": "sha256:73945dbbe8b0fe36b4f6c6cf9491d467f082e7f00d895b72825b3292fd58be6e", "role": "OPTIONAL"},
                    ],
                },
                {
                    "groupId": "A1-ORGANIC-DECLINE",
                    "when": {"path": "signalFlags", "op": "CONTAINS", "value": "organic_traffic_decline"},
                    "fields": [
                        {"canonicalField": "traffic.organic.decline_causes", "fieldHash": "sha256:68f03c148c4653174aaecf5c9a7647f2a284d61b3db48ae6e95a489afb218547", "role": "REQUIRED"},
                    ],
                },
                {
                    "groupId": "A1-CROSS-METRIC",
                    "when": {"path": "crossMetricRequired", "op": "TRUTHY"},
                    "fields": [
                        {"canonicalField": "diagnosis.cross_metric.patterns", "fieldHash": "sha256:806c448b743bde54c9dbbe19880bf2320bbf570b5e242d4e6ef46375e8bab6db", "role": "REQUIRED"},
                    ],
                },
            ],
        },
        {
            "compositionId": "KCT-AGENT2-EXECUTION-V1",
            "agent": "Agent2",
            "stage": "agent2_action_plan",
            "baseFields": [
                {"canonicalField": "platform.operation.context", "fieldHash": "sha256:9bafe89a2ae1ded2af9406aeaef9a5ca3ae8f2b7976d71c6daa87bbebed262e1", "role": "REQUIRED"},
                {"canonicalField": "category.operation.context", "fieldHash": "sha256:d0a34b0cd704be8147279b27a7d05631a5cb8b9166d5f0ef649c74da5c837311", "role": "REQUIRED"},
            ],
            "conditionalGroups": [
                {
                    "groupId": "A2-TITLE-IMAGE",
                    "when": {"path": "actionFamily", "op": "EQ", "value": "title_image_test"},
                    "fields": [{"canonicalField": "action.title_image.strategy", "fieldHash": "sha256:385ddd20be55f62b3322127f68757dac0edbdfed5fb5be2d429d60036bb1e95b", "role": "REQUIRED"}],
                },
                {
                    "groupId": "A2-ROAS-SCALE",
                    "when": {"path": "actionFamily", "op": "EQ", "value": "roas_scale"},
                    "fields": [{"canonicalField": "action.roas.scale.strategy", "fieldHash": "sha256:94dba96fbc9ea00b1945a4b5b1fe8e22c007460da9ccf40cc88edf5ea940aead", "role": "REQUIRED"}],
                },
                {
                    "groupId": "A2-ROAS-GUARD",
                    "when": {"path": "actionFamily", "op": "EQ", "value": "roas_guard"},
                    "fields": [{"canonicalField": "action.roas.guard.strategy", "fieldHash": "sha256:82b117f6ddb3271d4e265c43f733940a07cc4534ff45726a2224d3cb67d3b60d", "role": "REQUIRED"}],
                },
                {
                    "groupId": "A2-PLATFORM-ACTIVITY",
                    "when": {"path": "actionFamily", "op": "IN", "value": ["platform_activity", "activity_apply"]},
                    "fields": [{"canonicalField": "action.platform_activity.strategy", "fieldHash": "sha256:bd3575f9c9f816439044dc8441bcf99f14c8246516954ade9c9a785d739fbf99", "role": "REQUIRED"}],
                },
                {
                    "groupId": "A2-CONVERSION",
                    "when": {"path": "actionFamily", "op": "IN", "value": ["conversion_repair", "service_repair"]},
                    "fields": [{"canonicalField": "action.conversion_repair.strategy", "fieldHash": "sha256:0079779e79c64c07aaaf907f19778974775cb76bbe8e87bdda60684a5881b0d6", "role": "REQUIRED"}],
                },
                {
                    "groupId": "A2-EXPERIENCE",
                    "when": {"path": "experienceRequired", "op": "TRUTHY"},
                    "fields": [
                        {"canonicalField": "experience.positive.applicability", "fieldHash": "sha256:2f36f52c14daa6705f45b811c95bee80e473bd8d32d6f3b0da1cba17b12d05da", "role": "OPTIONAL"},
                        {"canonicalField": "experience.negative.risk", "fieldHash": "sha256:74577c3c8560ce09dab80fd4c0a58e7912bfc883cd08d366b0fa9ea58f0b4425", "role": "OPTIONAL"},
                    ],
                },
            ],
        },
        {
            "compositionId": "KCT-AGENT3-SOP-V1",
            "agent": "Agent3",
            "stage": "sop_compile",
            "baseFields": [
                {"canonicalField": "company.sop.execution_principles", "fieldHash": "sha256:6c337ecb041c552e350f8545aee6c3613e2d050f517106d8f965cb1b5ec441a4", "role": "REQUIRED"},
                {"canonicalField": "company.sop.task_timing", "fieldHash": "sha256:f2d30c98af32a63053ebb1be499fd6b88e952710f8085652bce69abbc11ccbc8", "role": "REQUIRED"},
            ],
            "conditionalGroups": [
                {
                    "groupId": "A3-BRAND",
                    "when": {"path": "customerFacing", "op": "TRUTHY"},
                    "fields": [{"canonicalField": "brand.expression.style", "fieldHash": "sha256:375f4ed437e8765424c5157582358add401019a3d2ee84b9e5a59d874e734787", "role": "OPTIONAL"}],
                },
                {
                    "groupId": "A3-HISTORY",
                    "when": {"path": "historicalCaseRequired", "op": "TRUTHY"},
                    "fields": [{"canonicalField": "company.sop.historical_cases", "fieldHash": "sha256:23c9344213ab934cfe644130342660b828b5a053fe134799da215cedc1ed30af", "role": "OPTIONAL"}],
                },
                {
                    "groupId": "A3-EXPERIENCE",
                    "when": {"path": "experienceRequired", "op": "TRUTHY"},
                    "fields": [
                        {"canonicalField": "experience.positive.applicability", "fieldHash": "sha256:2f36f52c14daa6705f45b811c95bee80e473bd8d32d6f3b0da1cba17b12d05da", "role": "OPTIONAL"},
                        {"canonicalField": "experience.negative.risk", "fieldHash": "sha256:74577c3c8560ce09dab80fd4c0a58e7912bfc883cd08d366b0fa9ea58f0b4425", "role": "OPTIONAL"},
                    ],
                },
            ],
        },
    ],
}


def rag_field_registry_v25() -> Dict[str, Any]:
    return deepcopy(_FIELD_PROJECTION)


def knowledge_composition_table_v25() -> Dict[str, Any]:
    return deepcopy(_COMPOSITION_PROJECTION)


__all__ = [
    "V25_KNOWLEDGE_RUNTIME_PROJECTION_VERSION",
    "rag_field_registry_v25",
    "knowledge_composition_table_v25",
]
