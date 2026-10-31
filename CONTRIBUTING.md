# Contributing

## Commit messages

Use [Conventional Commits](https://www.conventionalcommits.org/) for every commit:

```text
<type>[optional scope]: <imperative summary>
```

Common types:

- `feat`: add or extend user-visible behavior;
- `fix`: correct broken behavior;
- `refactor`: change structure without changing behavior;
- `test`: add or revise tests;
- `docs`: documentation only;
- `build`: build tooling or dependencies;
- `ci`: continuous-integration configuration;
- `chore`: maintenance that fits no other type.

Examples:

```text
feat(adapters): add a Hermes skill target
fix(install): preserve foreign links during uninstall
refactor(catalog): parse artifacts into typed models
test(install): cover checkout relocation
docs: explain upstream update semantics
```

Use `!` and a `BREAKING CHANGE:` footer when a commit intentionally breaks a public command or catalog format.

## Before committing

```bash
make check
```

Generated marketplace and lock files must be current. Do not include credentials, machine-specific state, or employer-only content.
