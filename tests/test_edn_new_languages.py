from crapper.analyze import analyze_files
from crapper.metrics import render_edn


def test_gdscript_and_cpp_rows_carry_path_and_line(tmp_path):
    gd = tmp_path / "game" / "hero.gd"
    gd.parent.mkdir()
    gd.write_text("func hit(a, b):\n\tif a and b:\n\t\tpass\n", encoding="utf-8")
    cpp = tmp_path / "game" / "board.cpp"
    cpp.write_text("int Board::place(int a) {\n  return a ? 1 : 0;\n}\n", encoding="utf-8")
    text = render_edn(analyze_files([gd, cpp], tmp_path, None))
    assert (
        '{:name "hit", :namespace "game.hero", :complexity 3, :coverage nil, '
        ':crap nil, :path "game/hero.gd", :line 1}'
    ) in text
    assert (
        '{:name "Board::place", :namespace "game.board", :complexity 2, '
        ':coverage nil, :crap nil, :path "game/board.cpp", :line 1}'
    ) in text
