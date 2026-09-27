from storyteller.cli import build_parser, main


def test_cli_help_lists_the_command_section(capsys):
    assert main([]) == 0

    output = capsys.readouterr().out
    assert "usage: st" in output
    assert "commands:" in output


def test_parser_accepts_help(capsys):
    parser = build_parser()

    try:
        parser.parse_args(["--help"])
    except SystemExit as error:
        assert error.code == 0
    else:
        raise AssertionError("--help should exit successfully")

    assert "100 TIMES AI STORYTELLER" in capsys.readouterr().out
