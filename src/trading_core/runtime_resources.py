"""Deliberately empty compatibility marker.

Azure table/queue/blob/resource names do not belong in public trading-core.
Private runtime adapters own those resource mappings and combine them with
`trading_core.ingestion` at their composition boundary.
"""
