"""Active three-Agent token runtime.

Agent1 keeps the strict V22.5.9 exact runtime, Agent2 keeps V22.5.20 exact output
acceptance, and Agent3 uses V23.2.15 structured auxiliary-condition repair.
"""
from src.services.agent_token_runtime_v22520_service import *
from src.services.agent3_runtime_v23215_service import run_agent3_sop_projected_inputs
