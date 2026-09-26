"""KapraOS AI layer — Step 1 foundation.

Architecture (see task brief)::

                         SHOPKEEPER
                             |
                             v
                  +--------------------+
                  |   MASTER AI AGENT  |
                  |    Deep Agent      |
                  +---------+----------+
                            |
                +-----------+------------+
                |                        |
                v                        v
            SKILLS                  ANALYTICS
                |                    SUBAGENT
                |                        |
                v                        v
        DOMAIN OPERATION           READ-ONLY SQL
             TOOLS                    ACCESS

Step 1 establishes boundaries only: one master Deep Agent, one core
skill, one read-only analytics subagent spec, a tiny demo tool
registry, and an operation policy. No real business mutations exist
yet — those arrive in later steps on top of the existing deterministic
services (SaleService, PurchaseService, inventory, payments, expenses,
reporting).
"""

__all__: list[str] = []
