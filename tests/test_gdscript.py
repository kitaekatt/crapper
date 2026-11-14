from crapper.discover import iter_source_files, language_of
from crapper.languages import functions_in_file
from crapper.languages.gdscript import functions_in_source

SAMPLE = """extends Node

signal done

static func pick(a):
	return a if a else 1

func every(x, y, z):
	if x and y or z:
		pass
	elif x:
		pass
	else:
		pass
	match x:
		1:
			pass
		_:
			pass
	for i in x:
		pass
	while x:
		pass
	var t = 1 if x else 2
	var g = func(): return x && y
	return t

func plain():
	return 1

class Inner:
	func m():
		pass

	static func sm(a):
		while a || a:
			pass

	class Deep:
		func d(k):
			if k:
				pass
"""


def test_every_branch_kind_is_counted():
    found = functions_in_source(SAMPLE, "src/demo/hero.gd", "/proj")
    assert [(fn.namespace, fn.name, fn.complexity) for fn in found] == [
        ("demo.hero", "pick", 2),
        ("demo.hero", "every", 11),
        ("demo.hero", "plain", 1),
        ("demo.hero.Inner", "m", 1),
        ("demo.hero.Inner", "sm", 3),
        ("demo.hero.Inner.Deep", "d", 2),
    ]


def test_location_and_language_are_recorded():
    found = functions_in_source(SAMPLE, "src/demo/hero.gd", "/proj")
    by_name = {fn.name: fn for fn in found}
    assert by_name["pick"].start_line == 5
    assert by_name["every"].start_line == 8
    assert by_name["d"].start_line == 40
    assert all(fn.language == "gdscript" and fn.path == "src/demo/hero.gd" for fn in found)


def test_dispatch_and_extension():
    assert language_of("player.gd") == "gdscript"
    found = functions_in_file("gdscript", "func a():\n\tpass\n", "a.gd", "/proj")
    assert [fn.name for fn in found] == ["a"]


def test_gd_files_are_discovered(tmp_path):
    (tmp_path / "a.gd").write_text("func a():\n\tpass\n", encoding="utf-8")
    assert [p.name for p in iter_source_files([tmp_path])] == ["a.gd"]
