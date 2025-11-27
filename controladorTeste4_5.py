from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, ether_types

class PolicingTestController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]  # Usa OpenFlow v1.3

    def __init__(self, *args, **kwargs):
        super(PolicingTestController, self).__init__(*args, **kwargs)
        self.mac_to_port = {}  # Tabela MAC → Porta por switch

    # Executado quando o switch se conecta ao controlador
    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath  # Switch
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        # Regra base: envia pacotes desconhecidos ao controlador
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER,
                                          ofproto.OFPCML_NO_BUFFER)]

        self.add_flow(datapath, 0, match, actions)

        self.logger.info("Switch %s conectado para o Teste 4.", datapath.id)

    # Função auxiliar para instalar regras de fluxo
    def add_flow(self, datapath, priority, match, actions, buffer_id=None):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        # Instrução: aplicar ações diretamente
        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]

        # Cria o FlowMod que o switch irá instalar
        mod = parser.OFPFlowMod(datapath=datapath,
                                buffer_id=buffer_id or ofproto.OFP_NO_BUFFER,
                                priority=priority,
                                match=match,
                                instructions=inst)

        datapath.send_msg(mod)

    # Handler chamado quando um pacote chega ao controlador
    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, ev):
        msg = ev.msg
        datapath = msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        in_port = msg.match['in_port']  # Porta onde o pacote chegou

        # Decodifica o pacote
        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)

        # Ignora LLDP (usado para descoberta de topologia)
        if eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return
        
        dst = eth.dst  # MAC destino
        src = eth.src  # MAC origem
        dpid = datapath.id  # ID do switch

        # Inicializa tabela MAC se necessário
        self.mac_to_port.setdefault(dpid, {})

        # Aprende o MAC de origem
        self.mac_to_port[dpid][src] = in_port

        # Procura porta do destino; se não souber, faz flood
        out_port = self.mac_to_port[dpid].get(dst, ofproto.OFPP_FLOOD)

        # Ação padrão: encaminhar para a porta de saída
        actions = [parser.OFPActionOutput(out_port)]

        # Se não for flood, instala regra no switch para evitar PacketIn
        if out_port != ofproto.OFPP_FLOOD:

            # Regra: porta de entrada e MAC destino
            match = parser.OFPMatch(in_port=in_port, eth_dst=dst)

            # Instala a regra com prioridade 1
            self.add_flow(datapath, 1, match, actions, msg.buffer_id)
            
        # Se o buffer do switch não contém os dados, envia manualmente
        data = msg.data if msg.buffer_id == ofproto.OFP_NO_BUFFER else None

        # Mensagem PacketOut para encaminhar o pacote imediatamente
        out = parser.OFPPacketOut(datapath=datapath,
                                  buffer_id=msg.buffer_id,
                                  in_port=in_port,
                                  actions=actions,
                                  data=data)

        datapath.send_msg(out)
