import time
from mininet.net import Mininet
from mininet.node import RemoteController, OVSKernelSwitch
from mininet.log import setLogLevel, info
from mininet.link import TCLink


# -------------------------------------------------------------------
# Configuração de QoS usando HTB + SFQ em s1-eth4 (link s1 → s2)
# Este método classifica tráfego por IP *de origem* e separa em duas
# classes: alta prioridade (H1) e baixa prioridade (H3).
# -------------------------------------------------------------------
def configure_qos_with_tc(net):
    s1 = net.get('s1')

    # Interface onde o QoS será aplicado (link entre s1 e s2)
    interface = 's1-eth4'
    info(f"*** Aplicando QoS via 'tc' na interface {interface}...\n")

    # Remove qualquer configuração antiga de qdisc
    s1.cmd(f'tc qdisc del dev {interface} root 2>/dev/null')

    # Cria disciplina raiz HTB com classe-padrão 20 (baixa prioridade)
    s1.cmd(f'tc qdisc add dev {interface} root handle 1: htb default 20')

    # Classe pai com capacidade total de 10 Mbps
    s1.cmd(f'tc class add dev {interface} parent 1: classid 1:1 htb rate 10mbit')

    # Classe de ALTA prioridade (H1)
    # - Reservada: 8 Mbps
    # - Pode usar até: 10 Mbps (ceil)
    # - Prioridade mais alta (prio 0)
    s1.cmd(f'tc class add dev {interface} parent 1:1 classid 1:10 '
           f'htb rate 8mbit ceil 10mbit prio 0')

    # Classe de BAIXA prioridade (H3)
    # - Reservada: 2 Mbps
    # - Pode usar até: 10 Mbps
    # - Prioridade menor (prio 1)
    s1.cmd(f'tc class add dev {interface} parent 1:1 classid 1:20 '
           f'htb rate 2mbit ceil 10mbit prio 1')

    # Filas internas das classes
    # SFQ usado para fairness dentro de cada classe
    s1.cmd(f'tc qdisc add dev {interface} parent 1:10 handle 10: sfq perturb 10')
    s1.cmd(f'tc qdisc add dev {interface} parent 1:20 handle 20: sfq perturb 10')

    # Filtros HTB baseados no IP *de origem*
    info("*** Aplicando filtros HTB (H1=alta, H3=baixa)...\n")

    # Trafego vindo de H1 (10.0.0.1) → classe de alta prioridade
    s1.cmd(f'tc filter add dev {interface} protocol ip parent 1: '
           f'prio 1 u32 match ip src 10.0.0.1 flowid 1:10')

    # Trafego vindo de H3 (10.0.0.3) → classe de baixa prioridade
    s1.cmd(f'tc filter add dev {interface} protocol ip parent 1: '
           f'prio 2 u32 match ip src 10.0.0.3 flowid 1:20')

    info("*** QoS configurado com sucesso.\n")


# -------------------------------------------------------------------
# Desativa offloading TCP (TSO/GSO/GRO) para evitar que o kernel
# agregue pacotes e estrague o comportamento do congestionamento.
# -------------------------------------------------------------------
def disable_tcp_offloading(net):
    info("*** Desativando TCP Offloading nos hosts...\n")
    for host in net.hosts:
        iface = f"{host.name}-eth0"
        host.cmd(f'ethtool -K {iface} tso off gso off gro off')


# -------------------------------------------------------------------
# Topologia e teste TCP:
#   H3 inicia fluxo TCP → baixa prioridade (classe 20)
#   Após 10s, H1 inicia fluxo TCP → alta prioridade (classe 10)
#
# Avalia se H1 "rouba" banda de H3, conforme esperado pelo HTB.
# -------------------------------------------------------------------
def run_testTCP():

    # Controller remoto (Ryu)
    c0 = RemoteController('c0', ip='127.0.0.1', port=6653)

    # Mininet com switches OVS e suporte a TCLink
    net = Mininet(controller=c0, switch=OVSKernelSwitch,
                  link=TCLink, autoSetMacs=True)
    net.addController(c0)

    info('*** Adicionando hosts e switches\n')

    # Hosts com IP fixo
    h1 = net.addHost('h1', ip='10.0.0.1/24')
    h2 = net.addHost('h2', ip='10.0.0.2/24')
    h3 = net.addHost('h3', ip='10.0.0.3/24')
    h4 = net.addHost('h4', ip='10.0.0.4/24')

    # Switches OF1.3
    s1 = net.addSwitch('s1', protocols='OpenFlow13')
    s2 = net.addSwitch('s2', protocols='OpenFlow13')

    info('*** Criando links\n')

    # H1, H2 e H3 ligados ao s1
    net.addLink(h1, s1)
    net.addLink(h2, s1)
    net.addLink(h3, s1)

    # Link principal (controlado por HTB)
    net.addLink(s1, s2, port1=4, port2=1)

    # s2 → H4 (destino final)
    net.addLink(s2, h4)

    info('*** Iniciando rede\n')
    net.build()
    net.start()

    # Evita que offloading atrapalhe a medição de congestionamento
    disable_tcp_offloading(net)

    info('*** Aguardando switches conectarem ao controlador...\n')
    time.sleep(5)

    # Aplica QoS configurado acima
    configure_qos_with_tc(net)

    info('*** Preparando servidores Iperf\n')

    # Limpa processos antigos
    h4.cmd('killall iperf 2>/dev/null')
    h4.cmd('rm /tmp/iperf_*.log 2>/dev/null')

    # Servidores TCP separados para cada classe
    h4.cmd('iperf -s -p 5001 -w 128K -i 1 > /tmp/iperf_h1_server.log &')
    h4.cmd('iperf -s -p 5002 -w 128K -i 1 > /tmp/iperf_h3_server.log &')

    info('*** INICIANDO TESTES TCP ***\n')

    # Fluxo de baixa prioridade começa primeiro
    info("[0s] Iniciando fluxo **baixa prioridade** H3 → H4 (classe 20)\n")
    h3.cmd('iperf -c 10.0.0.4 -p 5002 -w 128K -t 40 &')

    # Espera 10s para criar competição
    time.sleep(10)

    # Fluxo de alta prioridade começa depois
    info("[10s] Iniciando fluxo **alta prioridade** H1 → H4 (classe 10)\n")
    h1.cmd('iperf -c 10.0.0.4 -p 5001 -w 128K -t 20 &')

    info("Fluxos rodando... aguardando finalizar.\n")
    time.sleep(42)

    info('*** TESTE FINALIZADO ***\n')

    # Exibe logs dos servidores (lado do H4)
    print("\n--- RESULTADO TCP (H3 → H4 – baixa prioridade) ---")
    print(h4.cmd('cat /tmp/iperf_h3_server.log'))

    print("\n--- RESULTADO TCP (H1 → H4 – alta prioridade) ---")
    print(h4.cmd('cat /tmp/iperf_h1_server.log'))

    # Limpeza
    h4.cmd('killall iperf')

    info('*** Parando a rede\n')
    net.stop()


# -------------------------------------------------------------------
# Execução principal
# -------------------------------------------------------------------
if __name__ == '__main__':
    setLogLevel('info')
    run_testTCP()
