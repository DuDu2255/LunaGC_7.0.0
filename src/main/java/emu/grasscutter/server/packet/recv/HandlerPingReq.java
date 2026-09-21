package emu.grasscutter.server.packet.recv;

import emu.grasscutter.net.packet.*;
import emu.grasscutter.net.proto.PacketHeadOuterClass.PacketHead;
import emu.grasscutter.server.game.GameSession;
import emu.grasscutter.utils.ProtoRead;
import emu.grasscutter.server.packet.send.PacketPingRsp;

@Opcodes(PacketOpcodes.PingReq)
public class HandlerPingReq extends PacketHandler {

    // read by field number so a renumber cannot silently zero the ping
    private static final int F_CLIENT_TIME = 1;
    private static final int F_SEQ = 15;

    @Override
    public void handle(GameSession session, byte[] header, byte[] payload) throws Exception {
        PacketHead head = PacketHead.parseFrom(header);
        var clientTime = (int) ProtoRead.varint(payload, F_CLIENT_TIME);
        var seq = (int) ProtoRead.varint(payload, F_SEQ);

        session.updateLastPingTime(clientTime);

        session.send(new PacketPingRsp(head.getClientSequenceId(), clientTime, seq));
    }
}
