## Type of Change

<!-- Mark the relevant option with an "x" -->

- [ ] Bug fix (non-breaking change which fixes an issue)
- [ ] New feature (non-breaking change which adds functionality)
- [ ] Documentation update
- [ ] Code refactoring (no functional changes)
- [ ] Test addition or update
- [ ] Configuration change
- [ ] Style change (formatting, naming, etc.)

## Description

<!-- Provide a description of the changes in this PR -->

## Testing

<!-- Describe the tests you ran and how to reproduce them -->

## Screenshots or Command Outputs (if applicable)

<!-- Add screenshots to help explain your changes -->

N/A

## Checklist

<!-- Mark completed items with an "x" -->

### Code Quality

- [ ] My code follows the project's style guidelines (Google Style Guides)
- [ ] I have performed a self-review of my own code
- [ ] I have commented my code, particularly in hard-to-understand areas
- [ ] I have made corresponding changes to the documentation
- [ ] My changes generate no new warnings

### Testing

- [ ] I have added tests that prove my fix is effective or that my feature works
- [ ] New and existing unit tests pass locally with my changes
- [ ] I have checked test coverage and it hasn't decreased
- [ ] All tests are properly marked (`@pytest.mark.unit` or `@pytest.mark.integration`)
- [ ] `poetry run python scripts/dev.py check-local` passes on my OS
- [ ] `Python / required` passes for this revision, including native Windows/Linux/macOS tests and combined coverage
- [ ] I recorded the head/tested merge SHAs, run URL and any failed-check/repair evidence

### Type Safety & Linting

- [ ] I have added type hints to all new functions
- [ ] My code passes `make lint` without errors
- [ ] My code passes `make type-check` type checking without errors

### Security

- [ ] I have not committed any secrets or sensitive information
- [ ] I have not added any dependencies with known vulnerabilities
- [ ] I have reviewed the security implications of my changes

### Documentation

- [ ] I have updated the README.md if needed
- [ ] I have updated docstrings following Google style
- [ ] I have updated the API documentation if endpoints changed
