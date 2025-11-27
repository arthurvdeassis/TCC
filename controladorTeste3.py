from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, ether_types, ipv4

class Controlador(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]  # Usa OpenFlow v1.3

    def __init__(self, *args, **kwargs):
        super(Controlador, self).__init__(*args, **kwargs)
        self.mac_to_port = {}  # Tabela aprendida MAC → Porta por switch

    # Handler executado quando o switch conecta ao controlador
    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath  # Acesso ao switch
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        # Regra de prioridade 0 que envia pacotes desconhecidos ao controlador
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER, ofproto.OFPCML_NO_BUFFER)]

        self.add_flow(datapath, 0, match, actions)

        self.logger.info("Switch %s conectado e pronto para os testes de comparação.", datapath.id)

    # Função auxiliar para instalar regras de fluxo
    def add_flow(self, datapath, priority, match, actions, buffer_id=None):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        # Define instruções que aplicam ações diretamente
        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]

        # Cria a mensagem FlowMod a ser enviada ao switch
        mod = parser.OFPFlowMod(datapath=datapath,
                                buffer_id=buffer_id or ofproto.OFP_NO_BUFFER,
                                priority=priority,
                                match=match,
                                instructions=inst)
        datapath.send_msg(mod)

    # Handler chamado quando um pacote chega sem regra no switch
    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, ev):
        msg = ev.msg  # Mensagem OpenFlow
        datapath = msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        in_port = msg.match['in_port']  # Porta de entrada do pacote

        # Decodifica o pacote recebido
        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)

        # Ignora pacotes LLDP usados para descoberta de topologia
        if eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return
        
        dst = eth.dst  # MAC destino
        src = eth.src  # MAC origem
        dpid = datapath.id  # ID do switch

        # Garante que a tabela exista para o switch atual
        self.mac_to_port.setdefault(dpid, {})

        # Registra a porta onde o MAC de origem foi visto
        self.mac_to_port[dpid][src] = in_port

        # Verifica se conhece o destino; caso contrário, faz flood
        out_port = self.mac_to_port[dpid].get(dst, ofproto.OFPP_FLOOD)
        
        # Define a ação padrão: enviar para a porta de saída
        actions = [parser.OFPActionOutput(out_port)]

        # Se não for flood, instala regra no switch
        if out_port != ofproto.OFPP_FLOOD:

            # Regra de match básica: porta de entrada + MAC destino
            match = parser.OFPMatch(in_port=in_port, eth_dst=dst)
    
            # Tratamento especial apenas no SWITCH 1 enviando para porta 4
            if dpid == 1 and out_port == 4 and eth.ethertype == ether_types.ETH_TYPE_IP:
                
                ip = pkt.get_protocol(ipv4.ipv4)  # Extrai cabeçalho IPv4

                # Fluxo H2 → H4 deve receber prioridade ALTA (fila 1)
                if ip.src == '10.0.0.2':
                    self.logger.info("    Fluxo H2->H4 detectado. Direcionando para Fila de ALTA PRIORIDADE (1).")

                    # Match específico por IP
                    match = parser.OFPMatch(
                        eth_type=ether_types.ETH_TYPE_IP,
                        ipv4_src=ip.src,
                        ipv4_dst=ip.dst
                    )

                    # Ação: define Fila 1 + envia ao destino
                    actions = [
                        parser.OFPActionSetQueue(1),
                        parser.OFPActionOutput(out_port)
                    ]
                
                # Fluxos H1->H4 e H3->H4 recebem baixa prioridade (fila 2)
                elif ip.src == '10.0.0.1' or ip.src == '10.0.0.3':
                    self.logger.info(
                        "    Fluxo H%s->H4 detectado. Direcionando para Fila de BAIXA PRIORIDADE (2).",
                        ip.src[-1]  # Último dígito do IP indica o host
                    )

                    match = parser.OFPMatch(
                        eth_type=ether_types.ETH_TYPE_IP,
                        ipv4_src=ip.src,
                        ipv4_dst=ip.dst
                    )

                    actions = [
                        parser.OFPActionSetQueue(2),
                        parser.OFPActionOutput(out_port)
                    ]
            
            # Instala regra para os próximos pacotes do mesmo fluxo
            self.add_flow(datapath, 1, match, actions, msg.buffer_id)
        
        # Se não houver buffer no switch, envia os dados junto
        data = msg.data if msg.buffer_id == ofproto.OFP_NO_BUFFER else None

        # Envia o pacote imediatamente para a porta de saída
        out = parser.OFPPacketOut(datapath=datapath,
                                  buffer_id=msg.buffer_id,
                                  in_port=in_port,
                                  actions=actions,
                                  data=data)
        datapath.send_msg(out)
