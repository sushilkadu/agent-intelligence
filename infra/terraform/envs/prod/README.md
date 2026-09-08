# prod environment

Placeholder. This environment mirrors the structure of `../dev` (backend
config wired to a prod state bucket/lock table, plus a call into the
`network` module, likely with `single_nat_gateway = false` for
resilience) once we get there. Not implemented in Phase 0 -- only
`envs/dev` actually calls the `network` module for now.
