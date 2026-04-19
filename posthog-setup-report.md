<wizard-report>
# PostHog post-wizard report

The wizard has completed a deep integration of PostHog analytics for Agent on Demand. The project already had a solid PostHog foundation (`observability.py`, `init_posthog()` in `AppConfig.ready()`, and tracking across agents, environments, and sessions). This integration filled the remaining gaps: middleware configuration, three new events for key lifecycle actions, environment variables, and a dashboard with five insights.

## Changes made

| File | Change |
|---|---|
| `src/config/settings.py` | Added `posthog.integrations.django.PosthogContextMiddleware` to MIDDLEWARE |
| `src/agent_on_demand/ui/views.py` | Added `from agent_on_demand.observability import track` import and `user.registered` event |
| `src/agent_on_demand/views/sessions.py` | Added `session.deleted` event in `delete_session` |
| `src/agent_on_demand/views/environments.py` | Added `environment.deleted` event in `environment_delete` |
| `.env` | Set `POSTHOG_API_KEY` and `POSTHOG_HOST` |

## Events

| Event | Description | File |
|---|---|---|
| `user.registered` | A new user completes registration and logs in for the first time. | `src/agent_on_demand/ui/views.py` |
| `session.deleted` | A session record is permanently deleted by the user. | `src/agent_on_demand/views/sessions.py` |
| `environment.deleted` | An environment record is permanently deleted by the user. | `src/agent_on_demand/views/environments.py` |
| `agent.created` | An agent is created (already instrumented). | `src/agent_on_demand/views/agents.py` |
| `agent.updated` | An agent configuration is updated (already instrumented). | `src/agent_on_demand/views/agents.py` |
| `agent.archived` | An agent is archived (already instrumented). | `src/agent_on_demand/views/agents.py` |
| `environment.created` | An environment is created (already instrumented). | `src/agent_on_demand/views/environments.py` |
| `environment.updated` | An environment is updated (already instrumented). | `src/agent_on_demand/views/environments.py` |
| `environment.archived` | An environment is archived (already instrumented). | `src/agent_on_demand/views/environments.py` |
| `session.created` | A session is created and dispatched (already instrumented). | `src/agent_on_demand/views/sessions.py` |
| `session.prompt_sent` | A follow-up prompt is sent to an existing session (already instrumented). | `src/agent_on_demand/views/sessions.py` |
| `session.terminated` | A session is terminated by the user (already instrumented). | `src/agent_on_demand/views/sessions.py` |
| `session.completed` | A session turn completes successfully (already instrumented). | `src/agent_on_demand/session_service/tasks.py` |
| `session.failed` | A session turn fails (already instrumented). | `src/agent_on_demand/session_service/tasks.py` |

## Next steps

We've built some insights and a dashboard for you to keep an eye on user behavior, based on the events we just instrumented:

- **Dashboard**: [Analytics basics](https://us.posthog.com/project/378591/dashboard/1484731)
- **New User Registrations**: [View insight](https://us.posthog.com/project/378591/insights/lFY0VYxu)
- **Registration → First Session Funnel**: [View insight](https://us.posthog.com/project/378591/insights/oE8XeEjE)
- **Session Outcomes**: [View insight](https://us.posthog.com/project/378591/insights/kqulWU3B)
- **Sessions Created per Day**: [View insight](https://us.posthog.com/project/378591/insights/CmqTGMyN)
- **Agent Churn: Archived Agents**: [View insight](https://us.posthog.com/project/378591/insights/8I8GIQY0)

### Agent skill

We've left an agent skill folder in your project. You can use this context for further agent development when using Claude Code. This will help ensure the model provides the most up-to-date approaches for integrating PostHog.

</wizard-report>
