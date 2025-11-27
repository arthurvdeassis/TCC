from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet
from ryu.lib.packet import ethernet
from ryu.lib.packet import ether_types
from ryu.lib.packet import ipv4

class Controlador(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]  # Usa o OpenFlow 1.3

    def __init__(self, *args, **kwargs):
        super(Controlador, self).__init__(*args, **kwargs)
        self.mac_to_port = {}  # Tabela MAC-Porta por switch

    # Evento disparado quando o switch conecta e envia FeaturesReply
    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        
        # Instala regra *default* (prioridade 0) enviando pacotes desconhecidos ao controlador
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER,
                                          ofproto.OFPCML_NO_BUFFER)]
        
        # Adiciona a regra básica de encaminhamento ao controlador
        self.add_flow(datapath, 0, match, actions)
        self.logger.info("Switch %s conectado e pronto.", datapath.id)

    # Função genérica para instalar regras de fluxo no switch
    def add_flow(self, datapath, priority, match, actions, buffer_id=None):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        
        # Ações aplicadas diretamente (APPLY)
        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
        
        # Criação da estrutura FlowMod
        mod = parser.OFPFlowMod(datapath=datapath, buffer_id=buffer_id or ofproto.OFP_NO_BUFFER,
                                priority=priority, match=match,
                                instructions=inst)
        datapath.send_msg(mod)  # Envia ao switch

    # Tratamento de Packet-In (quando um pacote não possui regra no switch)
    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, ev):
        msg = ev.msg
        datapath = msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        in_port = msg.match['in_port']  # Porta de entrada do pacote

        # Decodifica o pacote
        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)

        # Ignora LLDP (descoberta topo)
        if eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return
            
        dst = eth.dst  # MAC destino
        src = eth.src  # MAC origem
        dpid = datapath.id  # ID do switch
        self.mac_to_port.setdefault(dpid, {})  # Inicializa tabela caso ainda não exista

        self.logger.info(">>> Packet-in no switch %s: %s -> %s (porta %s)", dpid, src, dst, in_port)

        # Atualiza a tabela MAC->Porta
        self.mac_to_port[dpid][src] = in_port

        # Verifica se já sabe para onde encaminhar o destino
        if dst in self.mac_to_port[dpid]:
            out_port = self.mac_to_port[dpid][dst]  # Porta conhecida
        else:
            out_port = ofproto.OFPP_FLOOD  # Caso contrário, faz flood
        
        actions = [parser.OFPActionOutput(out_port)]  # Define ação de saída

        # Se não for FLOOD, instala fluxo
        if out_port != ofproto.OFPP_FLOOD:
            match = parser.OFPMatch(in_port=in_port, eth_dst=dst)
            
            # Tratamento especial para tráfego IPv4
            if eth.ethertype == ether_types.ETH_TYPE_IP:
                ip = pkt.get_protocol(ipv4.ipv4)
                
                # Apenas no switch 1 e porta 4 ocorre diferenciação por filas
                if dpid == 1 and out_port == 4:
                    
                    # Fluxo H1 -> H4 recebe ALTA prioridade (fila 1)
                    if ip.src == '10.0.0.1' and ip.dst == '10.0.0.4':
                        self.logger.info("    Detectado fluxo H1->H4. Aplicando ALTA PRIORIDADE (Fila 1).")
                        match = parser.OFPMatch(eth_type=ether_types.ETH_TYPE_IP, ipv4_src=ip.src, ipv4_dst=ip.dst)
                        actions = [parser.OFPActionSetQueue(1), parser.OFPActionOutput(out_port)]

                    # Fluxo H3 -> H4 recebe BAIXA prioridade (fila 2)
                    elif ip.src == '10.0.0.3' and ip.dst == '10.0.0.4':
                        self.logger.info("    Detectado fluxo H3->H4. Aplicando BAIXA PRIORIDADE (Fila 2).")
                        match = parser.OFPMatch(eth_type=ether_types.ETH_TYPE_IP, ipv4_src=ip.src, ipv4_dst=ip.dst)
                        actions = [parser.OFPActionSetQueue(2), parser.OFPActionOutput(out_port)]
            
            # Instala regra com prioridade 1
            self.add_flow(datapath, 1, match, actions, msg.buffer_id)

        # Caso não exista buffer no switch, envia dados novamente no PacketOut
        data = None
        if msg.buffer_id == ofproto.OFP_NO_BUFFER:
            data = msg.data

        # Envia o pacote para a porta de saída
        out = parser.OFPPacketOut(datapath=datapath, buffer_id=msg.buffer_id,
                                  in_port=in_port, actions=actions, data=data)
        datapath.send_msg(out)
