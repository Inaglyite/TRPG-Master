"""Switch an engine session without leaving partial state on load failure."""

from src.ai.context import context_shadow
from src.storage.database_turn_journal import DatabaseTurnJournal


def switch_engine_context(engine, context) -> None:
    # prepare_session replaces session fields. Keep their original identities
    # (client, callbacks, history, pins, journal) if any preparation step fails.
    previous = engine.__dict__.copy()
    try:
        engine.context = context
        engine._skill_catalog_cache = engine._skill_pins_cache = None
        engine.turn_journal = DatabaseTurnJournal(
            context.world_dir, world_id=context.world_id, module_name=context.module_name
        )
        engine._active_turn_id = None
        engine.prepare_session()
    except Exception:
        engine.__dict__.clear()
        engine.__dict__.update(previous)
        raise
    context_shadow.forget_engine(engine)
