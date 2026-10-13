"""Exercise the production Bash verifier with deterministic RPCs and a fake clock."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

E2E = Path(__file__).resolve().parents[1]
REPO = E2E.parents[1]


def h(n):
    return "0x" + format(n, "064x")


MOCK = r'''#!/usr/bin/env python3
import json, os, pathlib, sys
root=pathlib.Path(os.environ["TEST_ROOT"])
state=json.loads((root/"state.json").read_text())
mode=os.environ["TEST_MODE"]
direction=os.environ["TEST_DIRECTION"]
clock=int((root/"clock").read_text())
name=pathlib.Path(sys.argv[0]).name
args=sys.argv[1:]
def h(n): return "0x"+format(n,"064x")
def save(): (root/"state.json").write_text(json.dumps(state))
def block(chain,n):
    suffix=1 if state.get("changed") else 0
    if chain==2 and state.get("empty_changed") and n<position()[1]: suffix=2
    return {"number":hex(n),"hash":h(chain*10000+n*10+suffix)}
def position(index=0):
    moved=1 if mode=="reorg_move" and state.get("changed") else 0
    return 100+index+moved,(400 if mode=="padded_large" else 200)+index*(3 if mode.startswith("split_gap") else 1)+moved
def post(index=0):
    return h(12 if state.get("changed") else 11+index)
def receipt(chain,tx):
    if mode=="missing_forever" and tx in (h(1),h(2)): return None
    if mode=="receipt_gap" and clock<1002 and tx in (h(1),h(2)): return None
    index=1 if mode.startswith("split") and tx in (h(2),h(12),h(22)) else 0
    l1,l2=position(index)
    n=None
    if tx in (h(1),h(2)) and chain==(1 if direction=="L1" else 2): n=l1 if chain==1 else l2
    if chain==1 and tx==post(index): n=l1
    if chain==2 and tx in (h(21),h(22)): n=l2
    if chain==2 and mode=="many_receipts" and tx in [h(i) for i in range(21,46)]: n=l2
    if n is None: return None
    b=block(chain,n)
    return {"transactionHash":tx,"blockNumber":hex(n),"blockHash":b["hash"],
            "transactionIndex":"0x0","status":"0x0" if mode=="delivery_reverted" and chain==2 and tx==h(21) else "0x1",
            "to":"0x"+"a"*40,"logs":[]}
def correlation(index=0):
    l1,l2=position(index)
    a,b=block(1,l1),block(2,l2)
    size=205 if mode=="padded_large" else 100 if mode.startswith("padded") else 1
    history=[block(2,n) for n in range(l2-size+1,l2+1)]
    return {"l1BlockNumber":a["number"],"l1BlockHash":a["hash"],"l1TransactionHash":post(index),
            "l1TransactionIndex":"0x0","matchedL2Block":b,"l2Blocks":history,
            "l2Range":{"firstBlockNumber":history[0]["number"],"lastBlockNumber":b["number"],
                       "firstBlockHash":history[0]["hash"],"lastBlockHash":b["hash"],"blockCount":hex(size)},
            "canonicalL2":True,"l2Finalized":False}
if name=="date":
    print(clock); sys.exit()
if name=="sleep":
    (root/"clock").write_text(str(clock+int(args[0]))); sys.exit()
if name=="cast":
    endpoint=args[args.index("--rpc-url")+1]
    chain=1 if endpoint.endswith("1") else 2
    if args[0]=="block-number":
        print(202 if chain==2 else 102); sys.exit()
    if args[0]=="receipt":
        r=None if mode=="block_receipts" else receipt(chain,args[1])
        if not r: sys.exit(1)
        print(int(r[args[2]],16) if args[2]=="blockNumber" else r[args[2]]); sys.exit()
    if args[0]=="tx":
        r=receipt(chain,args[1])
        if not r: sys.exit(1)
        print("0x0011" if args[2]=="input" else r[args[2]]); sys.exit()
    raise AssertionError(args)
if name=="forge":
    text=" ".join(args)
    state.setdefault("forge",[]).append(args)
    if ":VerifyL1BatchCalldata" in text:
        state["decodes"]=state.get("decodes",0)+1
        if mode in ("reorg_same_height","reorg_move") and not state.get("changed"):
            state["changed"]=True; save()
            print("FAIL: old inclusion changed during calldata verification"); sys.exit(1)
        if args[args.index("--fork-block-number")+1]=="9999":
            save(); print("FAIL: stale inclusion metadata"); sys.exit(1)
        if mode=="content_mismatch":
            save(); print("FAIL: unexpected return bytes"); sys.exit(1)
        print("PASS: posted entries and rolling hashes match")
        index=1 if "0x0012" in args and mode!="split_duplicate" else 0
        print("L1_CALLDATA_EXPECTED="+("2" if mode.startswith("split") else "1"))
        print("L1_CALLDATA_MATCHED=["+str(index)+"]")
    elif ":VerifyL1" in text:
        print("PASS: expected L1 evidence matched")
        print("L1_MATCH_BLOCK=100")
        print("L1_BATCH_TX="+post())
        print("L1_BATCH_TX_CANDIDATE="+post())
        if mode.startswith("split"): print("L1_BATCH_TX_CANDIDATE="+post(1))
    elif ":VerifyL2CallsInRange" in text:
        if mode=="discovery_missing":
            save(); print("FAIL: expected L2 calls missing"); sys.exit(1)
        matches=[position(i)[1] for i in range(2 if mode.startswith("split") else 1)]
        if mode=="split_gap_wrong": matches[0]+=1
        print("PASS: L2 calls found")
        print("L2_MATCH_BLOCKS="+("invalid" if mode=="discovery_malformed" else json.dumps(matches, separators=(",", ":"))))
        if mode=="discovery_reorg" and not state.get("changed"): state["changed"]=True
    elif ":VerifyL2Blocks" in text or ":VerifyL2Calls " in text:
        if mode.startswith("split"):
            expected=json.dumps([position(0)[1],position(1)[1]], separators=(",", ":"))
            assert expected in args,args
        key="L2_TABLE_TX" if ":VerifyL2Blocks" in text else "L2_CALL_TX"
        print("PASS: L2 semantic checks match")
        print(key+"="+h(21))
        if mode.startswith("split"): print(key+"="+h(22))
        if mode=="many_receipts" and key=="L2_CALL_TX":
            for i in range(22,46): print(key+"="+h(i))
    else: raise AssertionError(args)
    if mode=="reorg_after_pass" and ":VerifyL2Calls " in text and not state.get("changed"):
        state["changed"]=True
    if mode=="padded_empty_reorg" and ":VerifyL2Calls " in text:
        state["empty_changed"]=True
    save(); sys.exit()
assert name=="curl",name
def respond(request):
    method=request["method"]; params=request["params"]; endpoint=args[-1]
    chain=1 if endpoint.endswith("1") else 2
    state.setdefault("calls",[]).append([endpoint,method,params])
    result=None
    error=None
    if method.startswith("eez_"):
        if endpoint.startswith("direct") or mode=="unsupported": error={"code":-32601,"message":"Method not found"}
        elif mode=="invalid_params": error={"code":-32602,"message":"Invalid params"}
        elif mode=="catchup" and clock<1002: error={"code":-32001,"message":"Index catching up"}
        elif method=="eez_getCrossChainTransaction": result=None
        elif params==["0x1"]: result=None
        else:
            assert isinstance(params[0],dict) and "blockHash" in params[0],params
            matches=[]
            for index in range(2 if mode.startswith("split") else 1):
                c=correlation(index)
                expected=c["matchedL2Block"]["hash"] if method=="eez_getSettlementByL2Block" else c["l1BlockHash"]
                if params[0]["blockHash"]==expected: matches.append(c)
            result=(matches[0] if matches else None) if method=="eez_getSettlementByL2Block" else matches
    elif method=="eth_getBlockByNumber":
        result=block(chain,202 if chain==2 else 102) if params[0]=="latest" else block(chain,int(params[0],16))
    elif method in ("eth_getTransactionReceipt","eth_getTransactionByHash"):
        r=receipt(chain,params[0])
        if (mode=="delivery_indexes_missing" or mode.startswith("delivery_bad_")) and params[0] in (h(21),h(22)):
            result=None
        elif method=="eth_getTransactionReceipt":
            result=None if mode=="block_receipts" else r
        elif r:
            result={**r,"hash":params[0],"input":"0x0012" if params[0]==h(12) else "0x0011"}
    elif method=="eth_getTransactionByBlockHashAndIndex":
        for index in range(2 if mode.startswith("split") else 1):
            r=receipt(chain,post(index))
            if r and r["blockHash"]==params[0]: result={**r,"hash":post(index),"input":"0x0011"}
    elif method=="eth_getBlockReceipts":
        result=[r for tx in (h(1),h(2),post(),post(1),h(21),h(22))
                if (r:=receipt(chain,tx)) and r["blockHash"]==params[0]]
        result=list({r["transactionHash"]:r for r in result}.values())
        if chain==2:
            if mode=="many_receipts":
                result=[receipt(chain,h(i)) for i in range(21,46)]
                result[0]["logs"]=[{"data":"0x"+"ab"*100000}]
            if mode=="delivery_block_unsupported": error={"code":-32601,"message":"Unsupported"}
            if mode=="delivery_block_incomplete": result=[]
            for r in result:
                if r["transactionHash"]!=h(21): continue
                if mode=="delivery_bad_hash": r["blockHash"]=h(999)
                if mode=="delivery_bad_number": r["blockNumber"]="0x999"
                if mode=="delivery_bad_status": r["status"]=None
            if mode=="delivery_bad_duplicate": result+= [r for r in result if r["transactionHash"]==h(21)]
            if mode=="delivery_block_reorg" and not state.get("changed"): state["changed"]=True
    else:
        raise AssertionError((method,params))
    return {"jsonrpc":"2.0","id":request["id"],**({"error":error} if error else {"result":result})}
raw=args[args.index("--data")+1]
request=json.loads(sys.stdin.read() if raw=="@-" else raw)
state["http_requests"]=state.get("http_requests",0)+1
if isinstance(request,list):
    state.setdefault("batches",[]).append([args[-1],request[0]["method"],len(request)])
    if mode=="batch_unsupported":
        response={"jsonrpc":"2.0","id":None,"error":{"code":-32600,"message":"Batch unsupported"}}
    else:
        response=list(reversed([respond(item) for item in request]))
        if mode=="batch_missing": response=response[:-1]
        if mode=="batch_duplicate": response=response+response[:1]
        if mode=="batch_wrong_id": response[0]["id"]=999
        if mode=="batch_member_error":
            response[0]={"jsonrpc":"2.0","id":response[0]["id"],"error":{"code":-32000,"message":"Unavailable"}}
        if mode=="batch_null_primary" and args[-1].startswith("direct"):
            response[0]["result"]=None
        if mode=="padded_partial_reorg" and args[-1]=="direct2" and request[0]["method"]=="eth_getBlockByNumber" and len(response)>1:
            response[0]["result"]["hash"]=h(999)
            response[1]["result"]=None
else:
    response=respond(request)
save()
print(json.dumps(response))

'''


class NetworkEvidenceTests(unittest.TestCase):
    def run_verifier(self, mode="normal", direction="L2", runner=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "clock").write_text("1000")
            (root / "state.json").write_text("{}")
            binaries = root / "bin"
            binaries.mkdir()
            for name in ("curl", "cast", "forge", "date", "sleep"):
                path = binaries / name
                path.write_text(MOCK)
                path.chmod(0o755)
            job = root / "job"
            job.mkdir()
            count = 2 if mode.startswith("split") or mode=="multi_same_batch" else 1
            (job / "txs.txt").write_text("\n".join(h(i + 1) for i in range(count)) + "\n")
            (job / "compute.out").write_text("EXPECTED fixture")
            (job / "manifest.env").write_text(
                f"_TRIGGER_CHAIN={direction}\n_TX_COUNT={count}\n_HAS_COMPUTE=true\n"
                "_SENDER=0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n"
                "EXPECTED_L1_CALL_HASHES='[]'\nEXPECTED_L1_HASHES='[]'\n"
                "EXPECTED_L1_TABLE=0x01\nEXPECTED_L1_STEPS=0x1234\nEXPECTED_L1_STATIC_TABLE=0x\n"
                "EXPECTED_L2_HASHES='[0x01]'\n"
                f"EXPECTED_L2_CALL_HASHES='{('[]' if mode=='padded_table_only' else '[0x02]')}'\n"
                "EXPECTED_L2_TABLE=0x01\nEVENTLESS_L2_HASHES='[]'\nABSENT_L2_HASHES='[]'\n")
            (root / "snapshot.env").write_text("L1_BLOCK_BEFORE=90\nL2_BLOCK_BEFORE=190\n")
            (root / "Scenario.sol").write_text("contract ComputeExpected {}\n")
            cache = root / "cache"
            cache.mkdir()
            if mode=="stale_metadata":
                (cache / (h(11) + ".meta")).write_text("0x" + "a" * 40 + " 9999")
            result = subprocess.run(
                ["bash", str(runner or E2E / "lib/network-scenario.sh"), str(root / "Scenario.sol"),
                 "--l1-rpc", "direct1", "--l2-rpc", "direct2",
                 "--l1-front", "front1", "--l2-front", "front2",
                 "--rollups", "0x" + "a" * 40, "--manager-l2", "0x" + "b" * 40],
                cwd=REPO, env={**os.environ, "PATH": str(binaries) + ":" + os.environ["PATH"],
                    "TEST_ROOT": directory, "TEST_MODE": mode, "TEST_DIRECTION": direction,
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "E2E_STAGE": "verify", "E2E_JOB_DIR": str(job),
                    "E2E_SNAPSHOT": str(root / "snapshot.env"), "E2E_L1TX_CACHE": str(cache),
                    "E2E_VERIFY_TIMEOUT": "8", "E2E_CORRELATION": "auto"},
                capture_output=True, text=True, timeout=40)
            return result, json.loads((root / "state.json").read_text()), int((root / "clock").read_text())

    def assert_passes(self, mode="normal", direction="L2"):
        result, state, clock = self.run_verifier(mode, direction)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("====== Done ======", result.stdout)
        return result, state, clock

    def test_hash_correlation_in_both_directions_ignores_stale_metadata(self):
        for direction in ("L1", "L2"):
            with self.subTest(direction=direction):
                result, state, _ = self.assert_passes("stale_metadata", direction=direction)
                self.assertNotIn("9999", result.stdout)
                decodes = [args for args in state["forge"] if any(":VerifyL1BatchCalldata" in a for a in args)]
                self.assertEqual(decodes[0][decodes[0].index("--fork-block-number") + 1], "100")
                self.assertIn(h(21) + "  (block 200)", result.stdout)
                self.assertFalse(any(endpoint.startswith("direct") and method.startswith("eez_")
                                     for endpoint, method, _ in state["calls"]))

    def test_temporarily_missing_receipt_is_retried(self):
        _, _, clock = self.assert_passes("receipt_gap")
        self.assertEqual(clock, 1002)

    def test_block_receipts_resolve_missing_transaction_index(self):
        _, state, _ = self.assert_passes("block_receipts")
        self.assertTrue(any(method == "eth_getBlockReceipts" for _, method, _ in state["calls"]))

    def test_index_catchup_is_retried_without_disabling_correlation(self):
        result, state, clock = self.assert_passes("catchup")
        self.assertEqual(clock, 1002)
        self.assertNotIn("unsupported", result.stdout)
        self.assertTrue(any(isinstance(params[0], dict) for _, method, params in state["calls"]
                            if method == "eez_getSettlementByL2Block"))

    def test_unsupported_fronts_use_content_scan(self):
        result, _, _ = self.assert_passes("unsupported")
        self.assertIn("unsupported on Composer fronts", result.stdout)

    def test_same_height_and_moved_inclusions_discard_failed_pass(self):
        for mode in ("reorg_same_height", "reorg_move", "reorg_after_pass"):
            with self.subTest(mode=mode):
                result, state, _ = self.assert_passes(mode)
                self.assertEqual(state["decodes"], 2)
                self.assertIn("Waiting for fresh network evidence", result.stderr)
                self.assertIn("L1 postBatch:   " + h(12), result.stdout)

    def test_split_triggers_keep_both_settlements_and_deliveries(self):
        for direction in ("L1", "L2"):
            with self.subTest(direction=direction):
                result, state, _ = self.assert_passes("split", direction)
                self.assertEqual(state["decodes"], 2)
                self.assertEqual(result.stdout.count("L1 postBatch:"), 2)
                self.assertIn(h(22) + "  (block 201)", result.stdout)

    def test_same_batch_multi_trigger_retains_full_table_check(self):
        _, state, _ = self.assert_passes("multi_same_batch")
        decodes = [args for args in state["forge"] if any(":VerifyL1BatchCalldata" in a for a in args)]
        self.assertEqual(len(decodes), 1)
        self.assertEqual(decodes[0][-1], "false")

    def test_missing_delivery_indexes_use_correlated_block_receipts(self):
        _, state, _ = self.assert_passes("delivery_indexes_missing")
        self.assertTrue(any(endpoint=="direct2" and method=="eth_getBlockReceipts"
                            for endpoint, method, _ in state["calls"]))

    def test_duplicate_split_coverage_is_rejected(self):
        result, state, _ = self.run_verifier("split_duplicate")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(state["decodes"], 2)
        self.assertIn("matched by two settlement txs", result.stdout)
        self.assertNotIn("====== Done ======", result.stdout)

    def test_stable_content_mismatch_fails_without_retry(self):
        result, state, clock = self.run_verifier("content_mismatch")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(state["decodes"], 1)
        self.assertEqual(clock, 1000)
        self.assertNotIn("====== Done ======", result.stdout)

    def test_missing_receipt_has_one_absolute_deadline(self):
        result, _, clock = self.run_verifier("missing_forever")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(clock, 1008)
        self.assertIn("deadline expired", result.stderr)

    def test_range_discovery_checks_only_matching_blocks_but_revalidates_all_hashes(self):
        for mode, expected_block, count in (("padded", 200, 100), ("padded_large", 400, 205)):
            with self.subTest(mode=mode):
                _, state, _ = self.assert_passes(mode)
                verifiers = [args for args in state["forge"]
                             if any(a.endswith(":VerifyL2Blocks") or a.endswith(":VerifyL2Calls") for a in args)]
                self.assertTrue(verifiers)
                for args in verifiers:
                    self.assertIn(f"[{expected_block}]", args)
                scans = [args for args in state["forge"] if any(a.endswith(":VerifyL2CallsInRange") for a in args)]
                self.assertEqual(len(scans), 1)
                self.assertEqual(scans[0][-4:-2], [str(expected_block-count+1), str(expected_block)])
                self.assertFalse(any(method=="eth_getLogs" for _, method, _ in state["calls"]))
                self.assertLess(state["http_requests"], 40)
                self.assertTrue(all(size<=100 for _, _, size in state["batches"]))
                expected = {hex(n) for n in range(expected_block-count+1, expected_block+1)}
                checked = {params[0] for endpoint, method, params in state["calls"]
                           if endpoint.endswith("2") and method=="eth_getBlockByNumber"}
                self.assertTrue(expected.issubset(checked))

    def test_reorg_of_an_empty_correlated_block_still_restarts_verification(self):
        result, state, _ = self.assert_passes("padded_empty_reorg")
        self.assertEqual(state["decodes"], 2)
        self.assertEqual(result.stdout.count("====== Done ======"), 1)
        self.assertIn("discarding this pass", result.stderr)

    def test_batches_fall_back_when_explicitly_unsupported_or_primary_lags(self):
        for mode in ("batch_unsupported", "batch_null_primary"):
            with self.subTest(mode=mode):
                self.assert_passes(mode)

    def test_missing_duplicate_wrong_id_and_error_batch_members_never_pass(self):
        for mode in ("batch_missing", "batch_duplicate", "batch_wrong_id", "batch_member_error"):
            with self.subTest(mode=mode):
                result, _, _ = self.run_verifier(mode)
                self.assertEqual(result.returncode, 1, result.stdout+result.stderr)
                self.assertNotIn("====== Done ======", result.stdout)

    def test_partial_primary_reorg_cannot_be_hidden_by_stale_fallback(self):
        result, _, clock = self.run_verifier("padded_partial_reorg")
        self.assertEqual(result.returncode, 1, result.stdout+result.stderr)
        self.assertEqual(clock, 1008)
        self.assertNotIn("====== Done ======", result.stdout)

    def test_discovery_keeps_split_matches_without_accepting_gap_blocks(self):
        for direction in ("L1", "L2"):
            with self.subTest(direction=direction):
                self.assert_passes("split_gap", direction)
                result, _, _ = self.run_verifier("split_gap_wrong", direction)
                self.assertEqual(result.returncode, 1, result.stdout+result.stderr)
                self.assertIn("outside the matched settlements", result.stdout)
                self.assertNotIn("====== Done ======", result.stdout)

    def test_discovery_failure_or_malformed_matches_cannot_pass(self):
        for mode in ("discovery_missing", "discovery_malformed"):
            with self.subTest(mode=mode):
                result, _, _ = self.run_verifier(mode)
                self.assertEqual(result.returncode, 1, result.stdout+result.stderr)
                self.assertNotIn("====== Done ======", result.stdout)

    def test_reorg_during_discovery_restarts_with_fresh_receipts(self):
        result, state, _ = self.assert_passes("discovery_reorg")
        self.assertEqual(state["decodes"], 2)
        self.assertEqual(result.stdout.count("====== Done ======"), 1)
        self.assertIn("L1 postBatch:   " + h(12), result.stdout)

    def test_table_without_call_events_retains_settlement_coverage(self):
        _, state, _ = self.assert_passes("padded_table_only", "L1")
        self.assertFalse(any(any(a.endswith(":VerifyL2CallsInRange") for a in args) for args in state["forge"]))
        tables = [args for args in state["forge"] if any(a.endswith(":VerifyL2Blocks") for a in args)]
        self.assertEqual(len(tables), 1)
        self.assertIn(json.dumps(list(range(101, 201)), separators=(",", ":")), tables[0])

    def test_many_delivery_receipts_share_one_exact_block_read(self):
        result, state, _ = self.assert_passes("many_receipts")
        block_reads=[params for endpoint, method, params in state["calls"]
                     if endpoint.endswith("2") and method=="eth_getBlockReceipts"]
        self.assertEqual(block_reads, [[h(2*10000+200*10)]])
        delivery_hashes={h(i) for i in range(21,46)}
        self.assertFalse(any(method=="eth_getTransactionReceipt" and params[0] in delivery_hashes
                             for _, method, params in state["calls"]))
        for tx in delivery_hashes:
            self.assertIn(tx+"  (block 200)", result.stdout)

    def test_unavailable_block_receipts_use_individual_lookup(self):
        for mode in ("delivery_block_unsupported", "delivery_block_incomplete"):
            with self.subTest(mode=mode):
                _, state, _ = self.assert_passes(mode)
                self.assertTrue(any(method=="eth_getTransactionReceipt" and params==[h(21)]
                                    for _, method, params in state["calls"]))

    def test_invalid_block_receipts_cannot_satisfy_required_deliveries(self):
        for mode in ("delivery_bad_hash", "delivery_bad_number", "delivery_bad_status", "delivery_bad_duplicate"):
            with self.subTest(mode=mode):
                result, _, _ = self.run_verifier(mode)
                self.assertEqual(result.returncode, 1, result.stdout+result.stderr)
                self.assertNotIn("====== Done ======", result.stdout)

    def test_block_receipt_reorg_refreshes_the_whole_verification(self):
        result, state, _ = self.assert_passes("delivery_block_reorg")
        self.assertEqual(state["decodes"], 2)
        self.assertEqual(result.stdout.count("====== Done ======"), 1)

    def test_reverted_delivery_receipt_remains_a_failure(self):
        result, state, _ = self.run_verifier("delivery_reverted")
        self.assertEqual(result.returncode, 1, result.stdout+result.stderr)
        self.assertEqual(state["decodes"], 1)
        self.assertIn("matched L2 transaction reverted", result.stdout)
        self.assertNotIn("====== Done ======", result.stdout)

    def test_invalid_parameters_do_not_fall_back_or_loop(self):
        result, _, clock = self.run_verifier("invalid_params")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(clock, 1000)
        self.assertIn("malformed correlation RPC", result.stdout)


if __name__ == "__main__":
    unittest.main()

