# MCP server (fork)

`contrechamp_mcp/` lets an MCP client — Claude Code, or any agent that speaks
the Model Context Protocol over stdio — drive Contrechamp's tools and
pipelines. It is written for the MCP Python SDK 2.x (`mcp>=2.2`).

The client is an agent, not the human. The server is built on one rule: what
the agent sends is untrusted, and the decisions that belong to the human never
travel in the agent's arguments.

## Start it

```bash
python -m contrechamp_mcp                                  # from the repository
python /path/to/Contrechamp/contrechamp_mcp/__main__.py    # from anywhere
```

Claude Code (`.mcp.json` or `claude mcp add`):

```json
{
  "mcpServers": {
    "contrechamp": {
      "command": "/path/to/Contrechamp/.venv/bin/python",
      "args": ["/path/to/Contrechamp/contrechamp_mcp/__main__.py"]
    }
  }
}
```

Until 29 September 2026 the package was `openmontage_mcp`; update older client
configurations to `contrechamp_mcp`. Environment variables are now
`CONTRECHAMP_*`; the `OPENMONTAGE_*` names are still read (lib/env_names.py).

## Tools

| Tool | What it does |
|---|---|
| `list_capabilities` | light index: pipelines, and each tool's name, capability, provider, status |
| `describe_tool` | one tool's description and input schema |
| `create_project` | creates `projects/<id>/`; returns the `project_id` and the stages |
| `get_project_status` | completed stages, next stage, stages awaiting the human |
| `run_tool` | runs one tool inside a project; a call estimated over 5 s becomes a background job |
| `render_video` | renders from the project's `edit_decisions` and `asset_manifest`, always in the background |
| `get_job_status`, `list_jobs`, `cancel_job` | background jobs of this server process |
| `write_checkpoint` | records a stage; a gated stage asked `completed` is first stored `awaiting_human`, and the same call repeated puts that stored checkpoint to the human |
| `request_paid_tool_approval` | asks the human whether a paid tool may spend on this project |

## Guarantees, each with the test that fails without it

| Guarantee | How | Tests (`tests/mcp_server/`) |
|---|---|---|
| A call stays inside one project | `project_id` must be a kebab-case slug; every path, at any depth of the inputs, resolves inside `projects/<id>/` (symlinks followed); `~`, `../`, other absolute paths and any scheme under a path key (`https://../x` included) are refused; a file-like value that exists on disk is a path whatever its key; confined paths are passed to the tool absolute | `test_confine.py`, `test_project_id_cannot_climb_out_of_projects`, `test_a_tool_cannot_be_pointed_at_a_file_outside_the_project`, `test_a_web_url_cannot_smuggle_a_path` |
| The governance files are out of reach | tool paths must go through a subdirectory: `project.json`, `checkpoint_*.json`, `cost_log.json`, `events.jsonl`, `decision_log.json` and `history/` cannot be read or written by a tool | `test_governance_files_are_out_of_reach` |
| No code, filters or waivers | `custom_vf`, `custom_af` (ffmpeg filters can open any file), `extra_args`, `workflow_json`, `extra_params` are refused; `allow_unsafe_code` must stay false, `require_approval` cannot be switched off; a `*_tool` input must name an exposed tool | `test_inputs_that_carry_code_filters_or_waivers_are_refused`, `test_harmless_values_of_those_keys_pass` |
| The agent cannot approve | approvals are resolved parameters (`Resolve(...)` in the SDK): filled by a question to the client's user, absent from the tool's input schema | `test_the_approval_is_not_an_argument_the_model_can_fill`, `test_gated_stage_is_not_completed_when_the_human_refuses` |
| The human approves what they can read | the question names the stored `checkpoint_<stage>.json` and its fingerprint; other artifacts in the approving call are stored for review again, never approved; a project whose pipeline cannot be read refuses checkpoints | `test_gated_stage_completes_when_the_human_approves_what_is_stored`, `test_an_approval_cannot_be_obtained_for_a_swapped_payload`, `test_a_project_without_its_pipeline_refuses_checkpoints` |
| A client that cannot ask is not a yes | no elicitation capability → the call is refused and names the terminal command | `test_client_without_a_human_channel_cannot_complete_a_gate` |
| Every paid call meets the budget gate | the server forces `cap` mode, refuses to start with `CONTRECHAMP_BUDGET_DISABLED` or `CONTRECHAMP_APPROVE_TOOLS` (legacy `OPENMONTAGE_*` spellings included), and attributes each call to its project (`project_dir`) so the ceiling applies | `test_first_paid_use_waits_for_the_human_then_is_charged_to_the_project`, `test_the_ceiling_refuses_even_an_approved_tool`, `test_server_refuses_to_start_with_the_gate_bypassed`, `test_no_mode_name_can_undo_the_forced_cap`, `test_stdio_server_refuses_to_start_with_the_gate_disabled` |
| No publishing, no screen capture | tools with the `publish` or `screen_capture` capability are not exposed | `test_publishing_tools_are_not_exposed` |
| Cancellation does not lie | a running tool cannot be interrupted; `cancel_job` records the request and says the work, its files and its spend go on | `test_long_call_runs_in_the_background_and_cancel_is_honest` |

Each protection was removed in turn (mutation) and the listed tests failed.
An adversarial review found a first set of bypasses (a URL-shaped output
path, the governance files, ffmpeg filters, tools dispatching to hidden
tools, an approval not tied to what was stored); each is now a test above.

## Tried for real

With Claude Code 2.1.274 as the client, the server started over stdio and
connected. A project was created and a subtitle file written inside it; an
output path with `../../` was refused. A gated stage asked `completed` was
stored `awaiting_human` without a question. The same call, repeated, opened a
dialog in Claude Code naming the stored checkpoint and its fingerprint. The
call waited two minutes for the human, who read the checkpoint and approved;
the stage was then `completed` with `human_approved: true`, and the pending
version was kept in `history/`.

## Approving from a terminal

For clients that cannot relay a question to their user:

```bash
python -m contrechamp_mcp approve-stage <project_id> <stage>
python -m contrechamp_mcp approve-tool <project_id> <tool_name>
```

## Known limits

- **Elicitation trusts the client program.** The model cannot answer the
  question; the program that shows it to the user can. A client configured to
  answer elicitations automatically (a hook, a bot) approves in the human's
  name — that is the human's delegation, not the server's.
- **An agent with a shell is outside this boundary.** It can run the approval
  command, or call the tools without MCP. The server protects against an MCP
  client, not against a process with the human's user rights.
- **Jobs live in the server process.** A restart forgets them; a tool still
  running is not interrupted.
- **Network fetches are not filtered.** Tools that download from a URL
  (`video_downloader`, stock and music sources) accept any http(s) address,
  including ones on the local network.
- **Rendered HTML is not audited.** `hyperframes_compose` and the Remotion
  path of `video_compose` render content the client influences.
- **The gate trusts each tool's estimate.** A tool that estimates $0 for a
  call that costs money is not charged (a known case: ComfyUI partner nodes;
  custom ComfyUI graphs are refused through MCP).
- **A job that hangs keeps its slot** (two by default,
  `CONTRECHAMP_MCP_MAX_JOBS`) until the server restarts.
- The single-action ceiling (`single_action_approval_usd`) cannot be approved
  per call: an action above it is refused until the human raises the ceiling
  in `config.yaml`.

## Credit

The shape of this server — business-level tools, a job tracker, `run_tool`
over the tool registry — follows the MCP server of the
[openmontage-zh-mcp](https://github.com/noah-1106/openmontage-zh-mcp) fork by
noah-1106 (AGPL-3.0). This implementation is rewritten for mcp 2.x; it closes
three gaps of that server: `human_approved` accepted from the client,
`project_id` and paths taken as given, and paid calls outside any project.
