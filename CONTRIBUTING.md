# Contributing

Thanks for wanting to add a recipe. Here's what makes a good one.

## What Makes a Good Recipe

- **Solves a real problem** — not a toy example. Real production patterns.
- **Standalone** — `python recipe.py` works with no other context.
- **Actually tested** — the code runs. Not pseudocode.
- **Well explained** — the README explains the *why*, not just the *what*.

## Recipe Structure

Each recipe lives in `recipes/<name>/`:

```
recipes/my-pattern/
├── recipe.py    # the implementation
└── README.md    # the explanation
```

### recipe.py requirements

- Has a `main()` async function and `if __name__ == "__main__": asyncio.run(main())`
- Imports from `claude_code_sdk` — uses real SDK calls
- Has a demo that shows the pattern working
- Has docstrings on classes and functions
- No hardcoded API keys or secrets

### README.md requirements

- Starts with a one-line description of the pattern
- "The Problem" section — what breaks without this pattern
- "The Pattern" section — code snippet showing the core idea
- "Pitfalls" section — what goes wrong if you use this incorrectly
- "Running the Recipe" section — exact commands and expected output

## Adding Tests

Add test classes to `tests/test_imports.py`:

```python
class TestMyPattern:
    @pytest.fixture(scope="class")
    def module(self):
        return import_recipe("my-pattern")

    def test_my_class_exists(self, module):
        assert hasattr(module, "MyClass")

    def test_core_logic(self, module):
        result = module.my_function("input")
        assert result == "expected"
```

Tests should NOT make API calls. Test logic only.

## Submitting

1. Fork the repo
2. Add your recipe in `recipes/<name>/`
3. Add tests in `tests/test_imports.py`
4. Run `pytest -v` — all tests must pass
5. Open a PR with a title like `add recipe: <name>`

## Running Tests

```bash
pip install -e ".[dev]"
pytest -v
```
