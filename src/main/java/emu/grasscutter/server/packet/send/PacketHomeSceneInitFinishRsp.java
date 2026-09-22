package emu.grasscutter.server.packet.send;

import emu.grasscutter.net.packet.*;

// no Rsp proto exists for this one, so it answers empty rather than with the Req shape
public class PacketHomeSceneInitFinishRsp extends BasePacket {

    public PacketHomeSceneInitFinishRsp() {
        super(PacketOpcodes.HomeSceneInitFinishRsp);
    }
}
