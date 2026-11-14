import pytest

from crapper.discover import iter_source_files, language_of
from crapper.languages import functions_in_file
from crapper.languages.cpp import functions_in_source

SAMPLE = """namespace geo {

int branchy(int a, int b) {
  if (a && b || a) {
    return 1;
  } else if (b) {
    return 2;
  }
  for (int i = 0; i < 3; i++) {}
  for (auto x : xs) {}
  while (a) { a--; }
  do { a++; } while (a < 5);
  switch (a) { case 1: break; case 2: break; default: break; }
  try { a++; } catch (const E&) { } catch (...) { }
  if (a) goto done;
  done:
  return a ? 1 : 2;
}

class Shape {
 public:
  Shape() : n(0) { if (n) {} }
  ~Shape() { while (n) {} }
  int area() const { return n ? 1 : 0; }
  bool operator==(const Shape& o) const { return n == o.n && true; }
  auto cb = [](int x) { if (x) return 1; return 0; };
  struct Inner { void bm() { if (n) {} } };
  int declared();
  int n;
};

int Shape::declared() { return 1; }
Shape::~Shape() {}
Shape::Shape(int) {}
bool Shape::operator<(const Shape& o) const { if (n) return 1; return 0; }

template <typename T> T tmpl(T a) { if (a) return a; return a; }
template <typename T> struct Box { void m() {} };
template <typename T> void Box<T>::out() { if (1) {} }

auto lam = [](int x) { if (x) return 1; return 2; };
static auto lam2 = [&](int x) { return x; };

void use() {
  auto inner = [](int y) { if (y) return 1; return 0; };
  if (1) {}
}

}  // namespace geo
"""

EXPECTED = [
    ("branchy", 17),
    ("Shape::Shape", 2),
    ("Shape::~Shape", 2),
    ("Shape::area", 2),
    ("Shape::operator==", 2),
    ("Shape::cb", 2),
    ("Shape::Inner::bm", 2),
    ("Shape::declared", 1),
    ("Shape::~Shape", 1),
    ("Shape::Shape", 1),
    ("Shape::operator<", 2),
    ("tmpl", 2),
    ("Box::m", 1),
    ("Box::out", 2),
    ("lam", 2),
    ("lam2", 1),
    ("use", 2),
    ("inner", 2),
]


def test_every_branch_kind_and_function_kind():
    found = functions_in_source(SAMPLE, "src/demo/shape.cpp", "/proj")
    assert [(fn.name, fn.complexity) for fn in found] == EXPECTED
    assert {fn.namespace for fn in found} == {"demo.shape.geo"}


def test_location_and_language_are_recorded():
    found = functions_in_source(SAMPLE, "src/demo/shape.cpp", "/proj")
    by_name = {}
    for fn in found:
        by_name.setdefault(fn.name, fn)
    assert by_name["branchy"].start_line == 3
    assert by_name["Shape::area"].start_line == 25
    assert by_name["lam"].start_line == 46
    assert all(fn.language == "cpp" and fn.path == "src/demo/shape.cpp" for fn in found)


def test_word_operators_count():
    source = "int f(int a, int b) { return a and b or a; }\n"
    assert functions_in_source(source, "f.cpp")[0].complexity == 3


def test_c_header_is_read_as_cpp():
    source = "static inline int clamp(int v) { return v > 9 && v < 1 ? 9 : v; }\n"
    found = functions_in_source(source, "include/util.h", "/proj")
    assert [(fn.namespace, fn.name, fn.complexity) for fn in found] == [
        ("include.util", "clamp", 3)
    ]


def test_dispatch_and_extensions(tmp_path):
    for ext in (".cpp", ".cc", ".cxx", ".c++", ".h", ".hpp", ".hh", ".hxx"):
        assert language_of(f"a{ext}") == "cpp"
    found = functions_in_file("cpp", "void a() {}\n", "a.cpp", "/proj")
    assert [fn.name for fn in found] == ["a"]
    (tmp_path / "a.hpp").write_text("inline void a() {}\n", encoding="utf-8")
    assert [p.name for p in iter_source_files([tmp_path])] == ["a.hpp"]


@pytest.mark.parametrize("source", ["", "// comment only\n", "int x;\nvoid decl();\n"])
def test_no_definitions_no_functions(source):
    assert functions_in_source(source, "a.cpp") == []
