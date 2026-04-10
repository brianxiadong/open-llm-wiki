(function () {
  'use strict';

  if (typeof graphData === 'undefined' || !graphData.nodes) return;

  var container = document.getElementById('graph');
  var width = container.clientWidth;
  var height = container.clientHeight;

  var typeColors = {
    overview: '#4a90d9',
    source:   '#67b168',
    entity:   '#e07b53',
    concept:  '#9b6fcf',
    analysis: '#d4a94b'
  };

  var svg = d3.select('#graph')
    .append('svg')
    .attr('viewBox', [0, 0, width, height]);

  var simulation = d3.forceSimulation(graphData.nodes)
    .force('link', d3.forceLink(graphData.links).id(function (d) { return d.id; }).distance(80))
    .force('charge', d3.forceManyBody().strength(-200))
    .force('center', d3.forceCenter(width / 2, height / 2))
    .force('collision', d3.forceCollide().radius(30));

  var link = svg.append('g')
    .attr('stroke', '#ccc')
    .attr('stroke-opacity', 0.6)
    .selectAll('line')
    .data(graphData.links)
    .join('line')
    .attr('stroke-width', 1.5);

  var node = svg.append('g')
    .selectAll('g')
    .data(graphData.nodes)
    .join('g')
    .call(drag(simulation));

  node.append('circle')
    .attr('r', 8)
    .attr('fill', function (d) { return typeColors[d.type] || '#999'; })
    .attr('stroke', '#fff')
    .attr('stroke-width', 1.5);

  node.append('text')
    .text(function (d) { return d.label; })
    .attr('x', 12)
    .attr('y', 4)
    .attr('font-size', '12px')
    .attr('fill', 'var(--pico-color)');

  node.append('title')
    .text(function (d) { return d.label + ' (' + (d.type || '') + ')'; });

  node.style('cursor', 'pointer')
    .on('click', function (event, d) {
      if (d.url) window.location.href = d.url;
    });

  simulation.on('tick', function () {
    link
      .attr('x1', function (d) { return d.source.x; })
      .attr('y1', function (d) { return d.source.y; })
      .attr('x2', function (d) { return d.target.x; })
      .attr('y2', function (d) { return d.target.y; });
    node.attr('transform', function (d) {
      return 'translate(' + d.x + ',' + d.y + ')';
    });
  });

  function drag(sim) {
    return d3.drag()
      .on('start', function (event, d) {
        if (!event.active) sim.alphaTarget(0.3).restart();
        d.fx = d.x;
        d.fy = d.y;
      })
      .on('drag', function (event, d) {
        d.fx = event.x;
        d.fy = event.y;
      })
      .on('end', function (event, d) {
        if (!event.active) sim.alphaTarget(0);
        d.fx = null;
        d.fy = null;
      });
  }

  window.addEventListener('resize', function () {
    width = container.clientWidth;
    height = container.clientHeight;
    svg.attr('viewBox', [0, 0, width, height]);
    simulation.force('center', d3.forceCenter(width / 2, height / 2));
    simulation.alpha(0.3).restart();
  });
})();
