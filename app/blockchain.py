import socket
import threading
import os
import hashlib
import time
import subprocess
import json
from collections import Counter

STORAGE_PATH = "/storage"

# ==========================================
# P2P Node 核心類別
# ==========================================
class P2PNode:
    def __init__(self, ip, port, peers):
        self.ip = ip
        self.port = port
        self.peers = peers
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(('0.0.0.0', self.port))
        
        self.file_lock = threading.Lock()
        self.expected_hashes = {}
        self.awaiting_hashes = False
        
        self.log_buffer = []
        self.log_lock = threading.Lock()

        self.node_id = os.environ.get("NODE_NAME", f"{ip}-{port}")
        self.network_token = "MY_BLOCKCHAIN_SECRET_2026"

    def add_log(self, msg):
        print(msg)
        with self.log_lock:
            self.log_buffer.append(msg)

    def start(self):
        print(f"📡 P2P Listener starting at {self.ip}:{self.port}")
        threading.Thread(target=self._listen, daemon=True).start()

    def _listen(self):
        while True:
            try:
                data, addr = self.sock.recvfrom(65535)
                message = data.decode('utf-8')
                
                if message.startswith("TX:"):
                    parts = message.split(":")
                    if len(parts) == 4:
                        self._execute_transaction(parts[1], parts[2], parts[3])
                        self.add_log(f"[網路同步] 收到廣播交易: {parts[1]} 轉給 {parts[2]} {parts[3]} 元")
                    
                elif message.startswith("REQ_HASH"):
                    self.add_log(f"[跨節點驗證] 收到來自 {addr[0]} 的 Hash 請求，已回傳驗證結果。")
                    # 組合格式：RESP_HASH : [Hash] : [我的ID] : [安全Token]
                    response = f"RESP_HASH:{self._get_last_block_hash()}:{self.node_id}:{self.network_token}"
                    self.sock.sendto(response.encode('utf-8'), addr)
                    
                elif message.startswith("RESP_HASH:"):
                    if self.awaiting_hashes:
                        parts = message.split(":")
                        print(f"DEBUG: 收到原始回覆 -> {message}")

                        # 檢查格式是否完整 (RESP_HASH + Hash + ID + Token = 4 部分)
                        if len(parts) == 4:
                            h_val = parts[1]
                            sender_id = parts[2]
                            token = parts[3]

                            # 【資安門神】
                            # 1. 暗號不對 -> 滾 (防止外部封包)
                            # 2. 發送者不在通訊錄裡 -> 滾 (防止未授權節點)
                            if token == self.network_token:
                                # 使用 sender_id 作為 Key，防止灌票
                                self.expected_hashes[sender_id] = h_val

                elif message.startswith("BROADCAST_MAJORITY:"):
                    parts = message.split(":")
                    majority_hash = parts[1]
                    trustable_ip = parts[2]
                    my_hash = self._get_last_block_hash()
                    if my_hash != majority_hash:
                        self.add_log(f"[共識機制] ❌ 警告：本地帳本與全網共識不符！\n正在向信任節點 {trustable_ip} 請求修復...")
                        self.sock.sendto(b"REQ_SYNC", (trustable_ip, self.port))

                elif message.startswith("REQ_SYNC"):
                    self.add_log(f"[共識機制] 收到來自 {addr[0]} 的修復請求，正在傳送正確帳本資料...")
                    ledger_data = self._pack_ledger()
                    self.sock.sendto(f"RESP_SYNC:{ledger_data}".encode('utf-8'), addr)

                elif message.startswith("RESP_SYNC:"):
                    json_str = message[len("RESP_SYNC:"):]
                    self._unpack_and_repair_ledger(json_str)
                    self.add_log("🎉 [共識機制] 置換完成！本地帳本已成功依照 >50% 多數決共識修復！")

            except Exception as e:
                print(f"[Error] 監聽發生錯誤: {e}")

# ==========================================
# 帳本與共識邏輯 
# ==========================================
    def _execute_checkMoney(self, target, gui_mode=False):
        balance = 0
        with self.file_lock:
            files = sorted([f for f in os.listdir(STORAGE_PATH) if f.endswith(".txt") and f.split('.')[0].isdigit()], key=lambda x: int(x.split('.')[0]))
            for file in files:
                with open(f"{STORAGE_PATH}/{file}", "r") as f:
                    for line in f:
                        if "," in line:
                            parts = [p.strip() for p in line.split(",")]
                            if len(parts) == 3:
                                if parts[0] == target: balance -= int(parts[2])
                                if parts[1] == target: balance += int(parts[2])
        return balance

    def _execute_checkLog(self, target, gui_mode=False):
        logs = []
        with self.file_lock:
            files = sorted([f for f in os.listdir(STORAGE_PATH) if f.endswith(".txt") and f.split('.')[0].isdigit()], key=lambda x: int(x.split('.')[0]))
            for file in files:
                with open(f"{STORAGE_PATH}/{file}", "r") as f:
                    for line in f:
                        if "," in line and target in line: logs.append(line.strip())
        if gui_mode: return logs

    def _execute_checkChain(self, gui_mode=False, print_result=False):
        with self.file_lock:
            files = sorted([f for f in os.listdir(STORAGE_PATH) if f.endswith(".txt") and f.split('.')[0].isdigit()], key=lambda x: int(x.split('.')[0]))
            if len(files) <= 1: return (True, "✅ OK (目前僅有 1 個或 0 個區塊)") if gui_mode else True
            for i in range(1, len(files)):
                with open(f"{STORAGE_PATH}/{files[i-1]}", "rb") as f: actual_prev_hash = hashlib.sha256(f.read()).hexdigest()
                with open(f"{STORAGE_PATH}/{files[i]}", "r") as f: recorded_hash = f.readline().strip().replace("Sha256 of previous block: ", "")
                if actual_prev_hash != recorded_hash: return (False, f"❌ 帳本鍊受損！區塊：{files[i].split('.')[0]}") if gui_mode else False
            return (True, "✅ OK (本地帳本鍊完整)") if gui_mode else True

    def _get_last_block_hash(self):
        res = self._execute_checkChain()
        is_valid = res[0] if type(res) == tuple else res
        if not is_valid: return "INVALID"
        with self.file_lock:
            files = sorted([f for f in os.listdir(STORAGE_PATH) if f.endswith(".txt") and f.split('.')[0].isdigit()], key=lambda x: int(x.split('.')[0]))
            if not files: return "EMPTY"
            with open(f"{STORAGE_PATH}/{files[-1]}", "rb") as f: return hashlib.sha256(f.read()).hexdigest()

    def _pack_ledger(self):
        ledger_dict = {}
        with self.file_lock:
            for file in [f for f in os.listdir(STORAGE_PATH) if f.endswith(".txt")]:
                with open(f"{STORAGE_PATH}/{file}", "r") as f: ledger_dict[file] = f.read()
        return json.dumps(ledger_dict)

    def _unpack_and_repair_ledger(self, json_str):
        try:
            ledger_dict = json.loads(json_str)
            with self.file_lock:
                for f in os.listdir(STORAGE_PATH):
                    if f.endswith(".txt"): os.remove(os.path.join(STORAGE_PATH, f))
                for filename, content in ledger_dict.items():
                    with open(os.path.join(STORAGE_PATH, filename), "w") as f: f.write(content)
        except Exception as e: print(f"[Error] 解析失敗: {e}")

    def _execute_checkAllChains(self, target, gui_mode=False):
        # 1. 初始化收集箱
        self.expected_hashes.clear()
        self.awaiting_hashes = True

        # 2. 發送請求給所有人 (REQ_HASH)
        for peer in self.peers:
            self.sock.sendto(b"REQ_HASH", peer)

        # 3. 整合選票 (包含自己的一票)
        my_hash = self._get_last_block_hash()
        time.sleep(2) 
        self.awaiting_hashes = False
        
        all_votes = self.expected_hashes.copy()
        all_votes[self.node_id] = my_hash
        total_expected = len(self.peers) + 1
        
        output_msg = f"--- 實名制共識比對 (Token 驗證) --- \n"
        output_msg += f"預期節點: {total_expected} | 實際收到回覆: {len(all_votes)}\n"

        # 4. 統計出現次數最多的 Hash
        from collections import Counter
        hash_counts = Counter(all_votes.values())

        # 排除掉無效的 Hash (例如 INVALID 或 EMPTY)
        valid_hashes = {h: count for h, count in hash_counts.items() if h not in ["INVALID", "EMPTY"]}

        if not valid_hashes:
            return "❌ 系統不被信任：全網均無效帳本。" if gui_mode else None
        
        majority_hash, max_count = Counter(valid_hashes).most_common(1)[0]

        # 5. 判斷是否過半數
        if max_count > total_expected / 2:
            if my_hash == majority_hash:
                output_msg += f"\n✅ 全網達成共識 ({max_count}/{total_expected})！\n獎勵發放: 100 元 -> {target}"
                self._execute_transaction("SYSTEM", target, "100")
                # 廣播交易給所有人
                for peer in self.peers: 
                    self.sock.sendto(f"TX:SYSTEM:{target}:100".encode('utf-8'), peer)
            else:
                # 找一個持有正確 Hash 的節點 ID
                provider_id = [node_id for node_id, h in all_votes.items() if h == majority_hash][0]
                # 這裡需要注意：修復時需要真實 IP，所以我們需要從 all_nodes 找回對應的 (IP, Port)
                output_msg += f"\n⚠️ 本地帳本與多數不符！向 {provider_id} 請求帳本修復..."
                
                # 【關鍵】從全域通訊錄找出 provider_id 的真實地址
                if provider_id in ALL_NODES:
                    self.sock.sendto(b"REQ_SYNC", ALL_NODES[provider_id])
        else:
            output_msg += f"\n❌ 系統不被信任：無法達成過半數共識 (僅 {max_count}/{total_expected})。"
            
        if gui_mode: return output_msg

    def _execute_transaction(self, sender, receiver, amount):
        # 1. 如果是系統發錢 (SYSTEM)，不用檢查餘額
        if sender != "SYSTEM":
            # 2. 先呼叫我們剛才寫好的 checkMoney 查一下這個人剩多少錢
            res = self._execute_checkMoney(sender)
            # 防呆：如果是 None 就當作 0
            current_balance = res if res is not None else 0
            # 3. 檢查錢夠不夠
            if int(current_balance) < int(amount):
                raise ValueError(f"餘額不足！{sender} 目前只有 {current_balance} 元")
        
        tx_data = f"{sender}, {receiver}, {amount}\n"
        with self.file_lock:
            files = sorted([f for f in os.listdir(STORAGE_PATH) if f.endswith(".txt") and f.split('.')[0].isdigit()], key=lambda x: int(x.split('.')[0]))
            if not files: 
                curr_id, curr_path = 1, f"{STORAGE_PATH}/1.txt"
                with open(curr_path, "w") as f: f.write("Sha256 of previous block: 0\nNext block: None\n")
            else:
                curr_id, curr_path = int(files[-1].split('.')[0]), f"{STORAGE_PATH}/{files[-1]}"

            with open(curr_path, "r") as f: lines = f.readlines()
            if sum(1 for l in lines if "," in l) < 5:
                with open(curr_path, "a") as f: f.write(tx_data)
            else:
                new_id, new_path = curr_id + 1, f"{STORAGE_PATH}/{curr_id + 1}.txt"
                for i, line in enumerate(lines):
                    if line.startswith("Next block:"): lines[i] = f"Next block: {new_id}.txt\n"
                with open(curr_path, "w") as f: f.writelines(lines)
                with open(curr_path, "rb") as f: prev_hash = hashlib.sha256(f.read()).hexdigest()
                with open(new_path, "w") as f: f.write(f"Sha256 of previous block: {prev_hash}\nNext block: None\n{tx_data}")