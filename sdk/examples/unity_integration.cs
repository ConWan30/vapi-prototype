// VAPI SDK — Unity C# Integration
// Compatible: Unity 2022.3 LTS+, .NET Standard 2.1
//
// Attach VAPIManager to a persistent GameObject (DontDestroyOnLoad).
// Wire controller input to VAPIManager.IngestRecord() each physics frame.
//
// NuGet: Newtonsoft.Json (via Unity Package Manager)
// Package Manager: com.unity.nuget.newtonsoft-json

using System;
using System.Collections;
using System.Collections.Generic;
using System.Security.Cryptography;
using System.Text;
using Newtonsoft.Json;
using UnityEngine;
using UnityEngine.Networking;

namespace VAPI
{
    // -------------------------------------------------------------------------
    // Constants — mirror vapi_sdk.py POAC_RECORD_SIZE / inference codes
    // -------------------------------------------------------------------------

    public static class VAPIConstants
    {
        public const int RecordSize     = 228;
        public const int BodySize       = 164;
        public const int SigSize        = 64;
        public const int HashSize       = 32;

        // Inference codes
        public const byte INF_NOMINAL         = 0x20;
        public const byte INF_DRIVER_INJECT   = 0x28;  // L2 cheat
        public const byte INF_WALLHACK        = 0x29;  // L3 cheat
        public const byte INF_AIMBOT          = 0x2A;  // L3 cheat
        public const byte INF_TEMPORAL_BOT    = 0x2B;  // L5 advisory
        public const byte INF_BIO_ANOMALY     = 0x30;  // L4 advisory

        public static readonly byte[] CheatCodes = { INF_DRIVER_INJECT, INF_WALLHACK, INF_AIMBOT };

        public static string InferenceName(byte code) => code switch
        {
            0x20 => "NOMINAL",
            0x28 => "DRIVER_INJECT",
            0x29 => "WALLHACK_PREAIM",
            0x2A => "AIMBOT_BEHAVIORAL",
            0x2B => "TEMPORAL_ANOMALY",
            0x30 => "BIOMETRIC_ANOMALY",
            _    => $"UNKNOWN_0x{code:X2}"
        };

        public static bool IsCheat(byte code)
        {
            foreach (var c in CheatCodes)
                if (c == code) return true;
            return false;
        }
    }

    // -------------------------------------------------------------------------
    // VAPIRecord — zero-alloc parse of a 228-byte PoAC record
    // -------------------------------------------------------------------------

    public readonly struct VAPIRecord
    {
        public readonly byte[] Raw;           // 228 bytes
        public readonly byte   InferenceResult;
        public readonly byte   ActionCode;
        public readonly byte   Confidence;
        public readonly byte   BatteryPct;
        public readonly uint   MonotonicCtr;
        public readonly ulong  TimestampMs;
        public readonly double Latitude;
        public readonly double Longitude;
        public readonly uint   BountyId;
        public readonly byte[] RecordHash;    // SHA-256(raw[0..163])
        public readonly byte[] ChainHash;     // SHA-256(raw[0..227])
        public readonly byte[] PrevPoACHash;  // raw[0..31]

        public string InferenceName => VAPIConstants.InferenceName(InferenceResult);
        public bool   IsClean       => !VAPIConstants.IsCheat(InferenceResult) && InferenceResult != VAPIConstants.INF_TEMPORAL_BOT && InferenceResult != VAPIConstants.INF_BIO_ANOMALY;
        public bool   IsCheat       => VAPIConstants.IsCheat(InferenceResult);

        public VAPIRecord(byte[] raw)
        {
            if (raw.Length != VAPIConstants.RecordSize)
                throw new ArgumentException($"PoAC record must be {VAPIConstants.RecordSize} bytes, got {raw.Length}");

            Raw = raw;

            // Hash fields
            PrevPoACHash = new byte[32];
            Buffer.BlockCopy(raw, 0, PrevPoACHash, 0, 32);

            // Scalar fields at offset 128 (big-endian)
            InferenceResult = raw[128];
            ActionCode      = raw[129];
            Confidence      = raw[130];
            BatteryPct      = raw[131];
            MonotonicCtr    = ReadUInt32BE(raw, 132);
            TimestampMs     = ReadUInt64BE(raw, 136);
            Latitude        = ReadDoubleBE(raw, 144);
            Longitude       = ReadDoubleBE(raw, 152);
            BountyId        = ReadUInt32BE(raw, 160);

            // Computed hashes
            using var sha = SHA256.Create();
            RecordHash = sha.ComputeHash(raw, 0, VAPIConstants.BodySize);
            ChainHash  = sha.ComputeHash(raw);
        }

        public bool VerifyChainLink(VAPIRecord? prev)
        {
            if (prev == null)
            {
                // Genesis: prev_poac_hash must be all zeros
                foreach (var b in PrevPoACHash)
                    if (b != 0) return false;
                return true;
            }
            // Continuation: prev_poac_hash == prev.chain_hash
            for (int i = 0; i < VAPIConstants.HashSize; i++)
                if (PrevPoACHash[i] != prev.Value.ChainHash[i]) return false;
            return true;
        }

        // Big-endian helpers
        private static uint   ReadUInt32BE(byte[] b, int o) =>
            ((uint)b[o] << 24) | ((uint)b[o+1] << 16) | ((uint)b[o+2] << 8) | b[o+3];
        private static ulong  ReadUInt64BE(byte[] b, int o) =>
            ((ulong)ReadUInt32BE(b, o) << 32) | ReadUInt32BE(b, o + 4);
        private static double ReadDoubleBE(byte[] b, int o)
        {
            var buf = new byte[8];
            Buffer.BlockCopy(b, o, buf, 0, 8);
            if (BitConverter.IsLittleEndian) Array.Reverse(buf);
            return BitConverter.ToDouble(buf, 0);
        }
    }

    // -------------------------------------------------------------------------
    // VAPISession — primary integration surface
    // -------------------------------------------------------------------------

    public class VAPISession : IDisposable
    {
        private readonly List<VAPIRecord> _records = new();
        private readonly List<Action<VAPIRecord>> _cheatCallbacks = new();
        private bool _disposed;

        public int TotalRecords     => _records.Count;
        public int CleanRecords     { get; private set; }
        public int CheatDetections  { get; private set; }
        public int AdvisoryRecords  { get; private set; }

        public VAPISession OnCheatDetected(Action<VAPIRecord> callback)
        {
            _cheatCallbacks.Add(callback);
            return this;
        }

        public VAPIRecord IngestRecord(byte[] raw)
        {
            var rec = new VAPIRecord(raw);
            _records.Add(rec);

            if (rec.IsCheat)
            {
                CheatDetections++;
                foreach (var cb in _cheatCallbacks)
                    cb(rec);
            }
            else if (rec.InferenceResult == VAPIConstants.INF_TEMPORAL_BOT ||
                     rec.InferenceResult == VAPIConstants.INF_BIO_ANOMALY)
            {
                AdvisoryRecords++;
            }
            else
            {
                CleanRecords++;
            }
            return rec;
        }

        public bool ChainIntegrity()
        {
            if (_records.Count == 0) return true;
            if (!_records[0].VerifyChainLink(null)) return false;
            for (int i = 1; i < _records.Count; i++)
                if (!_records[i].VerifyChainLink(_records[i - 1])) return false;
            return true;
        }

        public void Dispose()
        {
            if (!_disposed)
            {
                _records.Clear();
                _disposed = true;
            }
        }
    }

    // -------------------------------------------------------------------------
    // VAPIManager — MonoBehaviour, attach to persistent game object
    // -------------------------------------------------------------------------

    public class VAPIManager : MonoBehaviour
    {
        [Header("VAPI Configuration")]
        [Tooltip("Your VAPI API key (obtain at api.vapi.gg/keys)")]
        public string ApiKey = "";

        [Tooltip("VAPI bridge URL (local or production)")]
        public string BridgeUrl = "http://localhost:8080/v1";

        [Tooltip("Device profile ID (leave blank for auto)")]
        public string ProfileId = "sony_dualshock_edge_v1";

        [Header("Events")]
        public UnityEngine.Events.UnityEvent<string> OnCheatDetected;
        public UnityEngine.Events.UnityEvent<SessionSummaryData> OnSessionSummary;

        private VAPISession _session;
        private string      _sessionId;
        private readonly List<byte[]> _pendingRecords = new();

        // ---- Lifecycle ----

        private void Awake()
        {
            DontDestroyOnLoad(gameObject);
        }

        private void Start()
        {
            StartCoroutine(CreateSession());
        }

        private void OnDestroy()
        {
            _session?.Dispose();
        }

        // ---- Public API ----

        /// <summary>
        /// Call once per physics frame with the raw 228-byte PoAC record
        /// received from your DualShock Edge companion app or bridge.
        /// </summary>
        public void IngestRecord(byte[] rawRecord)
        {
            if (_session == null) return;

            var rec = _session.IngestRecord(rawRecord);

            if (rec.IsCheat)
            {
                Debug.LogWarning($"[VAPI] Cheat detected: {rec.InferenceName} (confidence={rec.Confidence})");
                OnCheatDetected?.Invoke(rec.InferenceName);
            }

            // Queue for batch submission
            _pendingRecords.Add(rawRecord);
            if (_pendingRecords.Count >= 10)
                StartCoroutine(FlushPending());
        }

        /// <summary>
        /// Run SDK self-verification and log the SDKAttestation.
        /// Call once at game startup to confirm all PITL layers are wired.
        /// </summary>
        public void SelfVerify()
        {
            StartCoroutine(PostSelfVerify());
        }

        public SessionSummaryData GetSummary() => new()
        {
            TotalRecords    = _session?.TotalRecords ?? 0,
            CleanRecords    = _session?.CleanRecords ?? 0,
            CheatDetections = _session?.CheatDetections ?? 0,
            AdvisoryRecords = _session?.AdvisoryRecords ?? 0,
            ChainIntegrity  = _session?.ChainIntegrity() ?? false,
        };

        // ---- Coroutines ----

        private IEnumerator CreateSession()
        {
            _session = new VAPISession();
            _session.OnCheatDetected(rec =>
            {
                OnCheatDetected?.Invoke(rec.InferenceName);
            });

            if (string.IsNullOrEmpty(ApiKey)) yield break;

            var body = JsonConvert.SerializeObject(new
            {
                device_id  = SystemInfo.deviceUniqueIdentifier,
                profile_id = ProfileId,
                metadata   = new { game = Application.productName, platform = Application.platform.ToString() }
            });

            using var req = new UnityWebRequest($"{BridgeUrl}/sessions", "POST");
            req.uploadHandler   = new UploadHandlerRaw(Encoding.UTF8.GetBytes(body));
            req.downloadHandler = new DownloadHandlerBuffer();
            req.SetRequestHeader("Content-Type", "application/json");
            req.SetRequestHeader("Authorization", $"Bearer {ApiKey}");

            yield return req.SendWebRequest();

            if (req.result == UnityWebRequest.Result.Success)
            {
                var resp = JsonConvert.DeserializeObject<SessionCreateResponse>(req.downloadHandler.text);
                _sessionId = resp.session_id;
                Debug.Log($"[VAPI] Session created: {_sessionId} | tier={resp.phci_tier}");
                // Immediately self-verify
                StartCoroutine(PostSelfVerify());
            }
            else
            {
                Debug.LogError($"[VAPI] Failed to create session: {req.error}");
            }
        }

        private IEnumerator FlushPending()
        {
            if (_pendingRecords.Count == 0 || string.IsNullOrEmpty(ApiKey)) yield break;

            var batch = new List<string>();
            foreach (var r in _pendingRecords)
                batch.Add(Convert.ToBase64String(r));
            _pendingRecords.Clear();

            var body = JsonConvert.SerializeObject(new
            {
                device_id    = SystemInfo.deviceUniqueIdentifier,
                records_b64  = batch,
                session_id   = _sessionId,
                schema_version = 2
            });

            using var req = new UnityWebRequest($"{BridgeUrl}/records/batch", "POST");
            req.uploadHandler   = new UploadHandlerRaw(Encoding.UTF8.GetBytes(body));
            req.downloadHandler = new DownloadHandlerBuffer();
            req.SetRequestHeader("Content-Type", "application/json");
            req.SetRequestHeader("Authorization", $"Bearer {ApiKey}");

            yield return req.SendWebRequest();

            if (req.result != UnityWebRequest.Result.Success)
                Debug.LogWarning($"[VAPI] Batch submit failed: {req.error}");
        }

        private IEnumerator PostSelfVerify()
        {
            if (string.IsNullOrEmpty(_sessionId) || string.IsNullOrEmpty(ApiKey)) yield break;

            using var req = new UnityWebRequest($"{BridgeUrl}/sessions/{_sessionId}/self-verify", "POST");
            req.downloadHandler = new DownloadHandlerBuffer();
            req.SetRequestHeader("Authorization", $"Bearer {ApiKey}");

            yield return req.SendWebRequest();

            if (req.result == UnityWebRequest.Result.Success)
            {
                var att = JsonConvert.DeserializeObject<SDKAttestationResponse>(req.downloadHandler.text);
                Debug.Log($"[VAPI] Self-verify OK | all_layers={att.all_layers_active} | active={att.active_layer_count}/4");
                if (!att.all_layers_active)
                    Debug.LogWarning("[VAPI] Not all PITL layers are active — check bridge configuration");
            }
        }

        // ---- Response DTOs ----

        [Serializable] private class SessionCreateResponse
        {
            public string session_id;
            public string phci_tier;
        }

        [Serializable] private class SDKAttestationResponse
        {
            public bool   all_layers_active;
            public int    active_layer_count;
            public string attestation_hash_hex;
        }
    }

    // ---- Data structs exposed to Inspector / Events ----

    [Serializable]
    public struct SessionSummaryData
    {
        public int  TotalRecords;
        public int  CleanRecords;
        public int  CheatDetections;
        public int  AdvisoryRecords;
        public bool ChainIntegrity;
    }
}
