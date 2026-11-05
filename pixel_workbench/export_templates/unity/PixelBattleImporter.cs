using System;
using System.Collections.Generic;
using System.IO;
using UnityEditor;
using UnityEditor.Animations;
using UnityEngine;

namespace PixelBattle.EditorTools
{
    [Serializable]
    public class PixelBattleManifest
    {
        public string asset_name;
        public int fps = 12;
        public int width;
        public int height;
        public bool loop;
        public float pixels_per_unit = 16f;
        public string[] frames = new string[0];
        public PixelBattleSound[] sounds = new PixelBattleSound[0];

        public static PixelBattleManifest Load(string folder)
        {
            string path = Path.Combine(folder, "pixelbattle.json");
            if (!File.Exists(path))
            {
                return null;
            }
            return JsonUtility.FromJson<PixelBattleManifest>(File.ReadAllText(path));
        }
    }

    [Serializable]
    public class PixelBattleSound
    {
        public string file;
        public int start_frame;
        public float volume = 1f;
        public string label;
    }

    /// <summary>Imports PNGs in "&lt;sequence&gt;/frames/" as point-filtered, uncompressed sprites.</summary>
    public class PixelBattleTexturePreprocessor : AssetPostprocessor
    {
        void OnPreprocessTexture()
        {
            string path = assetPath.Replace('\\', '/');
            if (!path.Contains("/frames/"))
            {
                return;
            }
            string sequenceFolder = Path.GetDirectoryName(Path.GetDirectoryName(path));
            PixelBattleManifest manifest = PixelBattleManifest.Load(sequenceFolder);
            if (manifest == null)
            {
                return;
            }
            TextureImporter importer = (TextureImporter)assetImporter;
            importer.textureType = TextureImporterType.Sprite;
            importer.spriteImportMode = SpriteImportMode.Single;
            importer.spritePixelsPerUnit = manifest.pixels_per_unit > 0 ? manifest.pixels_per_unit : 16f;
            importer.filterMode = FilterMode.Point;
            importer.textureCompression = TextureImporterCompression.Uncompressed;
            importer.mipmapEnabled = false;
            importer.alphaIsTransparency = true;
        }
    }

    public static class PixelBattleImporter
    {
        const string MenuPath = "Assets/Pixel Battle/Build Animation";

        [MenuItem(MenuPath)]
        static void BuildFromSelection()
        {
            int built = 0;
            foreach (UnityEngine.Object selected in Selection.objects)
            {
                string folder = SequenceFolder(AssetDatabase.GetAssetPath(selected));
                if (folder != null && Build(folder))
                {
                    built++;
                }
            }
            if (built == 0)
            {
                EditorUtility.DisplayDialog("Pixel Battle", "pixelbattle.json が入っているフォルダ、または pixelbattle.json を選択してから実行してください。", "OK");
            }
        }

        [MenuItem(MenuPath, true)]
        static bool CanBuild()
        {
            foreach (UnityEngine.Object selected in Selection.objects)
            {
                if (SequenceFolder(AssetDatabase.GetAssetPath(selected)) != null)
                {
                    return true;
                }
            }
            return false;
        }

        static string SequenceFolder(string assetPath)
        {
            if (string.IsNullOrEmpty(assetPath))
            {
                return null;
            }
            string folder = AssetDatabase.IsValidFolder(assetPath) ? assetPath : Path.GetDirectoryName(assetPath);
            folder = folder.Replace('\\', '/');
            return File.Exists(Path.Combine(folder, "pixelbattle.json")) ? folder : null;
        }

        /// <summary>Creates &lt;name&gt;.anim, &lt;name&gt;.controller and &lt;name&gt;.prefab in the sequence folder.</summary>
        public static bool Build(string folder)
        {
            PixelBattleManifest manifest = PixelBattleManifest.Load(folder);
            if (manifest == null || manifest.frames == null || manifest.frames.Length == 0)
            {
                Debug.LogError("Pixel Battle: pixelbattle.json が読めないか、コマがありません: " + folder);
                return false;
            }
            float fps = Mathf.Max(1, manifest.fps);
            string name = string.IsNullOrEmpty(manifest.asset_name) ? Path.GetFileName(folder) : manifest.asset_name;

            List<Sprite> sprites = new List<Sprite>();
            foreach (string frame in manifest.frames)
            {
                string framePath = folder + "/frames/" + frame;
                AssetDatabase.ImportAsset(framePath, ImportAssetOptions.ForceUpdate);
                Sprite sprite = AssetDatabase.LoadAssetAtPath<Sprite>(framePath);
                if (sprite == null)
                {
                    Debug.LogError("Pixel Battle: スプライトとして読み込めません: " + framePath);
                    return false;
                }
                sprites.Add(sprite);
            }

            AnimationClip clip = new AnimationClip();
            clip.frameRate = fps;
            EditorCurveBinding binding = EditorCurveBinding.PPtrCurve("", typeof(SpriteRenderer), "m_Sprite");
            // One extra key holds the last frame for its full duration.
            ObjectReferenceKeyframe[] keys = new ObjectReferenceKeyframe[sprites.Count + 1];
            for (int i = 0; i < sprites.Count; i++)
            {
                keys[i] = new ObjectReferenceKeyframe { time = i / fps, value = sprites[i] };
            }
            keys[sprites.Count] = new ObjectReferenceKeyframe { time = sprites.Count / fps, value = sprites[sprites.Count - 1] };
            AnimationUtility.SetObjectReferenceCurve(clip, binding, keys);
            AnimationClipSettings settings = AnimationUtility.GetAnimationClipSettings(clip);
            settings.loopTime = manifest.loop;
            AnimationUtility.SetAnimationClipSettings(clip, settings);

            List<AudioClip> audioClips = new List<AudioClip>();
            List<AnimationEvent> events = new List<AnimationEvent>();
            if (manifest.sounds != null)
            {
                foreach (PixelBattleSound sound in manifest.sounds)
                {
                    string soundPath = folder + "/sounds/" + sound.file;
                    AudioClip audioClip = AssetDatabase.LoadAssetAtPath<AudioClip>(soundPath);
                    if (audioClip == null)
                    {
                        Debug.LogWarning("Pixel Battle: 効果音を読み込めません（Unityが対応していない形式の可能性があります）: " + soundPath);
                        continue;
                    }
                    audioClips.Add(audioClip);
                    AnimationEvent animationEvent = new AnimationEvent();
                    animationEvent.time = sound.start_frame / fps;
                    animationEvent.functionName = "PlayCue";
                    animationEvent.intParameter = audioClips.Count - 1;
                    animationEvent.floatParameter = sound.volume;
                    events.Add(animationEvent);
                }
            }
            AnimationUtility.SetAnimationEvents(clip, events.ToArray());

            string clipPath = folder + "/" + name + ".anim";
            string controllerPath = folder + "/" + name + ".controller";
            string prefabPath = folder + "/" + name + ".prefab";
            AssetDatabase.DeleteAsset(clipPath);
            AssetDatabase.DeleteAsset(controllerPath);
            AssetDatabase.CreateAsset(clip, clipPath);
            AnimatorController controller = AnimatorController.CreateAnimatorControllerAtPathWithClip(controllerPath, clip);

            GameObject root = new GameObject(name);
            try
            {
                SpriteRenderer renderer = root.AddComponent<SpriteRenderer>();
                renderer.sprite = sprites[0];
                Animator animator = root.AddComponent<Animator>();
                animator.runtimeAnimatorController = controller;
                AudioSource source = root.AddComponent<AudioSource>();
                source.playOnAwake = false;
                PixelBattle.PixelBattleCuePlayer player = root.AddComponent<PixelBattle.PixelBattleCuePlayer>();
                player.cues = audioClips.ToArray();
                GameObject prefab = PrefabUtility.SaveAsPrefabAsset(root, prefabPath);
                Selection.activeObject = prefab;
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(root);
            }
            AssetDatabase.SaveAssets();
            Debug.Log("Pixel Battle: " + prefabPath + " を作成しました（" + sprites.Count + "コマ、効果音" + audioClips.Count + "個）。");
            return true;
        }
    }
}
