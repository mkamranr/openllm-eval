# OpenLLM-Eval documentation

## Using OpenLLM-Eval

- [Getting started](user-guide/getting-started.md): deploy, connect a model, import a dataset,
  run a benchmark, evaluate quality, and read the results.
- [Dataset format](user-guide/datasets.md): case types, fields, CSV conventions, and examples.
- [Authentication and roles](user-guide/authentication.md): local accounts, Active Directory,
  Windows single sign-on, API tokens, and recovery commands.
- [Tool sandbox](user-guide/tool-sandbox.md): integration-mode tool calls executed by declared
  tools in an isolated container.

## Reference

- [Configuration](reference/configuration.md): environment variables in `.env` and `secrets.env`.
- [CLI](reference/cli.md): the `openllm-eval` command and the YAML run config.
- [REST API](reference/api.md): endpoints, error format, pagination, and Server-Sent Events.

## Development

- [Developer setup](developer-guide/setup.md): Docker deployment, local development, migrations,
  the mock model server, and Windows notes.
- [Architecture overview](architecture/overview.md): services, backend layout, and run execution.
- [Data model](architecture/data-model.md): tables and the reasoning behind them.
