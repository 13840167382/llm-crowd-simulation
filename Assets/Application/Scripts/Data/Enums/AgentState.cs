using System;

namespace CrowdSim.Data.Enums
{
    public enum AgentState
    {
        Walking,
        Queuing,
        Idle,
        Transitioning,
    }

    public static class AgentStateMapper
    {
        public static bool TryParse(string raw, out AgentState state)
        {
            switch ((raw ?? string.Empty).Trim().ToLowerInvariant())
            {
                case "walking":
                    state = AgentState.Walking;
                    return true;
                case "queuing":
                    state = AgentState.Queuing;
                    return true;
                case "idle":
                    state = AgentState.Idle;
                    return true;
                case "transitioning":
                    state = AgentState.Transitioning;
                    return true;
                default:
                    state = default;
                    return false;
            }
        }

        public static string ToApiValue(this AgentState state)
        {
            switch (state)
            {
                case AgentState.Walking:
                    return "walking";
                case AgentState.Queuing:
                    return "queuing";
                case AgentState.Idle:
                    return "idle";
                case AgentState.Transitioning:
                    return "transitioning";
                default:
                    throw new ArgumentOutOfRangeException(nameof(state), state, "Unknown agent state");
            }
        }
    }
}
