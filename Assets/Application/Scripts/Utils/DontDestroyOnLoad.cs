using UnityEngine;

namespace CrowdSim.Utils
{
    /// <summary>
    /// Add this component to an object and it'll persist across scenes 
    /// </summary>
    public sealed class DontDestroyOnLoad : MonoBehaviour
    {
        /// <summary>
        /// On Awake we make sure our object will not destroy on the next scene load
        /// </summary>
        private void Awake()
        {
            DontDestroyOnLoad(this.gameObject);
        }
    }
}