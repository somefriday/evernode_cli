// (C) Sergey Tyurin (Custler) 2024-05-13 10:00:00

pragma ton-solidity >= 0.60.0;
pragma AbiHeader expire;

// NodeInfo structure
struct NodeInfo {
    string  NodeVersion;            // 000050015 -> means 0.50.15 - each number converted to 3 digit number
    string  PrevNodeVersion;        // 000050013 -> means 0.50.13 - each number converted to 3 digit number
    string  NodeRepository;         // "https://github.com/everx-labs/ever-node.git"
    string  NodeBranch;             // master
    string  LastCommit;             // c7b2a7af27063cdd0414944a8c34ceb63c7f9dba
    string  PrevCommit;             // cc96e3938763e640cca86c62a4e66167581ec4f3
    uint8   SupportedBlock;         // 59
    uint8   PrevSupportedBlock;     // 58
    string  DockerImageTAG;         // custom-ever-node-502
    string  DockerImageRepo;        // everx/ever-node:custom-ever-node ; DockerImageName-DockerImageVersion = everx/ever-node:custom-ever-node-502
    bool    UpdateByCron;           // true - if allowed autoupdate by cron; false - if it is must be updated by hands
    uint32  UpdateStartTime;        // Time to start update  period (UNNIX time)
    uint32  UpdateDuration;         // time period for all network node for update. 
                                    //    For example, set it for 2 weeks, nodes will update during 2 weeks interval divided by 256. 
                                    //    Eeach node will update in time according to byte in middle of validator address.
                                    //    If set to 0, nodes will updates in one or two elections round
    string  MinCLIversion;          // min tonos-cli version for Custler's scripts
    bool    DisableOldNodeValidate;
}

contract CurrentNodeInfo {
    NodeInfo public curr_node_info;     // Info structure
    uint8  public code_ver;             // Version of the contract
    uint32 public code_updated_time;    // UNIIX time
    uint32 public info_updated_time;    // UNIIX time
    string public ABI_hex;              /* xz compressed ABI file converted to hex string
                                           - pack:
                                                xz -z -k -9 -e -T0 LastNodeInfo.abi.json
                                                xxd -ps LastNodeInfo.abi.json.xz|tr -d '\n' > LastNodeInfo.abi.hex
                                            - unpack:
                                                xxd -r -p LastNodeInfo.abi.hex > lnm.xz
                                                xz -d lnm.xz
                                        */
    
                                        // OLD ABI pack/unpack
                                        /* Store the contract json ABI as 7z archive converted to hex string
                                           - pack:  
                                                7za a -m0=ppmd LastNodeInfo.abi.7z LastNodeInfo.abi.json
                                                xxd -ps LastNodeInfo.abi.7z|tr -d '\n' > LastNodeInfo.abi.hex
                                           - unpack:
                                                xxd -r -p LastNodeInfo.abi.hex > lnm.7z
                                                7za x lnm.7z
                                        */
    
    /*
    Exception codes:
        901 - code deploy time is early then the contract published time ))
        902 - node info deploy time is early code deploy time
        903 - new info update time less then current info updated time
        904 - new code update time less then current code updated time
    */

    // Modifier that allows public function to be called only by message signed with owner's pubkey.
    modifier checkPubkeyAndAccept {
    require(tvm.pubkey() != 0, 101, "Public key is zero!");
	require(msg.pubkey() == tvm.pubkey(), 102, "Public key mismatch");
	tvm.accept();
	_;
    }

    constructor(NodeInfo initial_node_info, uint32 code_deploy_time, uint32 info_deploy_time, string initial_ABI) public  {
        require(code_deploy_time > 1653553322, 901);
        require(info_deploy_time >= code_deploy_time, 902);
        tvm.accept();
        code_ver = 1;
        curr_node_info = initial_node_info;
        ABI_hex = initial_ABI;
        code_updated_time = code_deploy_time;
        info_updated_time = info_deploy_time;
    }

    function change_node_info(NodeInfo new_node_info, uint32 new_info_time) external checkPubkeyAndAccept {
        require(new_info_time > info_updated_time, 903);
        info_updated_time = new_info_time;
        curr_node_info = new_node_info;
    }

    function getLastNodeInfo() external view returns (NodeInfo node_info) {
        return curr_node_info;
    }

function getABI() external view returns (string ABI) {
        return ABI_hex;
    }

    function getALLinfo() external view returns (
        NodeInfo node_info,
        uint8 _code_ver,
        uint32 _code_updated_time,
        uint32 _info_updated_time
    ) {
        return (curr_node_info, code_ver, code_updated_time, info_updated_time);
    }

    // ###########################################################################
    function updateContractCode(TvmCell newcode, uint32 new_code_time, string new_ABI) external checkPubkeyAndAccept {
        require(new_code_time > code_updated_time, 904);
	    tvm.setcode(newcode);
	    tvm.setCurrentCode(newcode);
        TvmCell stateVars = abi.encode(code_ver, new_ABI, new_code_time, info_updated_time);
        onCodeUpgrade(stateVars); 
    }

    function onCodeUpgrade(TvmCell stateVars) private {
        tvm.resetStorage();
        (uint8 version, string _new_ABI, uint32 _new_code_time, uint32 _info_updated_time) = abi.decode(stateVars, (uint8, string, uint32, uint32));
        code_ver = version + 1;
        code_updated_time = _new_code_time;
        info_updated_time = _info_updated_time;
        ABI_hex = _new_ABI;
    }
}
