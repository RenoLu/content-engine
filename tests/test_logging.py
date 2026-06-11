import logging

import content_engine.logging_setup as ls


def test_explicit_level_applies_after_initial_configuration():
    # Simulate import-time configuration at the default level...
    ls._CONFIGURED = False
    ls.setup_logging()  # defaults to INFO
    assert logging.getLogger().level == logging.INFO

    # ...then the CLI passing an explicit --log-level must take effect.
    ls.setup_logging("ERROR")
    assert logging.getLogger().level == logging.ERROR

    # restore something sane for the rest of the suite
    ls.setup_logging("WARNING")
    assert logging.getLogger().level == logging.WARNING


def test_get_logger_is_namespaced():
    log = ls.get_logger("content_engine.test")
    assert log.name == "content_engine.test"
