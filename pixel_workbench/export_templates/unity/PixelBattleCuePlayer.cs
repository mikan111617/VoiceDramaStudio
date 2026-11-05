using UnityEngine;

namespace PixelBattle
{
    /// <summary>
    /// Plays the sound cues of a Pixel Battle animation.
    /// The importer adds one AnimationEvent per cue: intParameter = cue index, floatParameter = volume.
    /// </summary>
    [RequireComponent(typeof(AudioSource))]
    public class PixelBattleCuePlayer : MonoBehaviour
    {
        public AudioClip[] cues = new AudioClip[0];

        AudioSource source;

        void Awake()
        {
            source = GetComponent<AudioSource>();
        }

        public void PlayCue(AnimationEvent animationEvent)
        {
            if (source == null)
            {
                source = GetComponent<AudioSource>();
            }
            int index = animationEvent.intParameter;
            if (source == null || cues == null || index < 0 || index >= cues.Length || cues[index] == null)
            {
                return;
            }
            source.PlayOneShot(cues[index], Mathf.Clamp(animationEvent.floatParameter, 0f, 2f));
        }
    }
}
