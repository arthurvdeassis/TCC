import time
from mininet.net import Mininet
from mininet.node import RemoteController, OVSKernelSwitch
from mininet.log import setLogLevel, info
from mininet.link import TCLink

def configure_policing_and_qos(net):
    s1 = net.get('s1')
    
    # ------------------------------
    # POLICIAMENTO DE INGRESSO (TBF)
    # ------------------------------
    info("*** Configurando Policiamento de Ingresso para H1 em s1-eth1...\n")
    ingress_if = 's1-eth1'   # Interface onde o tráfego vindo de H1 entra no switch
    ifb_if = 'ifb0'          # Interface virtual IFB para redirecionamento

    # Carrega o módulo IFB e ativa a interface ifb0
    s1.cmd('modprobe ifb numifbs=1')
    s1.cmd(f'ip link set dev {ifb_if} up')

    # Cria um qdisc de ingresso que captura pacotes
    s1.cmd(f'tc qdisc add dev {ingress_if} handle ffff: ingress')

    # Redireciona TODO tráfego de ingresso de s1-eth1 para a interface ifb0
    s1.cmd(f'tc filter add dev {ingress_if} parent ffff: protocol ip u32 match u32 0 0 action mirred egress redirect dev {ifb_if}')

    # Aplica um policiamento TBF na interface ifb0 (limitando H1 a 2 Mbps)
    s1.cmd(f'tc qdisc add dev {ifb_if} root tbf rate 2mbit burst 20k latency 50ms')

    # ---------------------------------
    # PRIORIZAÇÃO DE EGRESO (HTB)
    # ---------------------------------
    egress_if = 's1-eth4'
    info(f"*** Configurando Priorização de Egresso em {egress_if}...\n")
    
    # Remove qualquer configuração existente
    s1.cmd(f'tc qdisc del dev {egress_if} root')

    # Cria qdisc HTB raiz com classe default 30
    s1.cmd(f'tc qdisc add dev {egress_if} root handle 1: htb default 30')

    # Classe pai com 10 Mbps de banda total
    s1.cmd(f'tc class add dev {egress_if} parent 1: classid 1:1 htb rate 10mbit')

    # Classe 1:10 – alta prioridade (2 Mbps garantidos)
    s1.cmd(f'tc class add dev {egress_if} parent 1:1 classid 1:10 htb rate 2mbit ceil 10mbit prio 1')

    # Classe 1:20 – prioridade média (6 Mbps garantidos)
    s1.cmd(f'tc class add dev {egress_if} parent 1:1 classid 1:20 htb rate 6mbit ceil 10mbit prio 2')

    # Classe 1:30 – baixa prioridade (2 Mbps garantidos)
    s1.cmd(f'tc class add dev {egress_if} parent 1:1 classid 1:30 htb rate 2mbit ceil 10mbit prio 3')

    # --------------------------
    # FILTROS PARA CADA HOST
    # --------------------------
    info("*** Aplicando filtros de egresso...\n")

    # H1 → Classe alta prioridade
    s1.cmd(f'tc filter add dev {egress_if} protocol ip parent 1:0 prio 1 u32 match ip src 10.0.0.1 flowid 1:10')

    # H2 → Classe média prioridade
    s1.cmd(f'tc filter add dev {egress_if} protocol ip parent 1:0 prio 2 u32 match ip src 10.0.0.2 flowid 1:20')

    # H3 → Classe baixa prioridade
    s1.cmd(f'tc filter add dev {egress_if} protocol ip parent 1:0 prio 3 u32 match ip src 10.0.0.3 flowid 1:30')
    
    info("*** Configuração completa de Policiamento e QoS concluída.\n")

# ============================================================
# EXECUTA O TESTE UDP (policiamento + priorização)
# ============================================================
def run_testUDP():
    # Cria o controlador remoto
    c0 = RemoteController('c0', ip='127.0.0.1', port=6653)

    # Cria rede Mininet com OVS + TCLink
    net = Mininet(controller=c0, switch=OVSKernelSwitch, link=TCLink, autoSetMacs=True)
    net.addController(c0)

    # Hosts + switches
    info('*** Adicionando hosts e switches\n')
    h1 = net.addHost('h1', ip='10.0.0.1/24')
    h2 = net.addHost('h2', ip='10.0.0.2/24')
    h3 = net.addHost('h3', ip='10.0.0.3/24')
    h4 = net.addHost('h4', ip='10.0.0.4/24')
    s1 = net.addSwitch('s1', protocols='OpenFlow13')
    s2 = net.addSwitch('s2', protocols='OpenFlow13')

    # Ligações físicas (topologia)
    info('*** Criando links\n')
    net.addLink(h1, s1, port1=0, port2=1)
    net.addLink(h2, s1, port1=0, port2=2)
    net.addLink(h3, s1, port1=0, port2=3)
    net.addLink(s1, s2, port1=4, port2=1)
    net.addLink(s2, h4, port1=2, port2=0)

    # Inicialização da rede
    info('*** Iniciando a rede\n')
    net.build()
    net.start()
    
    info('*** Aguardando switches se conectarem ao controlador...\n')
    time.sleep(5)
    
    # Aplica policing + htb
    configure_policing_and_qos(net)
    
    # Servidores UDP no h4 para cada host
    info('*** Preparando o teste automatizado com UDP...\n')
    h4.cmd('iperf -s -u -p 5001 -i 1 > /tmp/iperf_h1_server.log &')
    h4.cmd('iperf -s -u -p 5002 -i 1 > /tmp/iperf_h2_server.log &')
    h4.cmd('iperf -s -u -p 5003 -i 1 > /tmp/iperf_h3_server.log &')
    
    info('*** INICIANDO TESTE COM POLICIAMENTO E PRIORIZAÇÃO ***\n')
    
    # Fluxo H1 – prioridade alta + policiado
    info("--> [Tempo 0s] Iniciando fluxo H1 -> H4 (policiado a 2Mbps, alta prioridade)\n")
    h1.cmd('iperf -c 10.0.0.4 -p 5001 -u -b 15m -t 50 &')
    
    time.sleep(10)

    # Fluxo H2 – prioridade média
    info("--> [Tempo 10s] Fluxo H2 ENTRA (média prioridade)\n")
    h2.cmd('iperf -c 10.0.0.4 -p 5002 -u -b 15m -t 30 &')
    
    time.sleep(10)

    # Fluxo H3 – baixa prioridade
    info("--> [Tempo 20s] Fluxo H3 ENTRA (baixa prioridade)\n")
    h3.cmd('iperf -c 10.0.0.4 -p 5003 -u -b 15m -t 20 &')
    
    info("--> Testes em andamento... Aguardando 35 segundos para a conclusão final.\n")
    time.sleep(35)
    
    # Exibição dos resultados coletados nos logs
    info('*** TESTE CONCLUÍDO ***\n\n')
    print('--- Resultados do Fluxo H1 -> H4 (Policiado / Alta Prioridade) ---')
    print(h4.cmd('cat /tmp/iperf_h1_server.log'))

    print('--- Resultados do Fluxo H2 -> H4 (Média Prioridade) ---')
    print(h4.cmd('cat /tmp/iperf_h2_server.log'))

    print('--- Resultados do Fluxo H3 -> H4 (Baixa Prioridade) ---')
    print(h4.cmd('cat /tmp/iperf_h3_server.log'))

    # Limpa processos e logs
    h4.cmd('killall iperf')
    h4.cmd('rm /tmp/iperf_*.log')

    # Finaliza rede
    info('*** Parando a rede\n')
    net.stop()

if __name__ == '__main__':
    setLogLevel('info')
    run_testUDP()
