"use strict";

$("[data-checkboxes]").each(function () {
  var me = $(this),
    group = me.data('checkboxes'),
    role = me.data('checkbox-role');

  me.change(function () {
    var all = $('[data-checkboxes="' + group + '"]:not([data-checkbox-role="dad"])'),
      checked = $('[data-checkboxes="' + group + '"]:not([data-checkbox-role="dad"]):checked'),
      dad = $('[data-checkboxes="' + group + '"][data-checkbox-role="dad"]'),
      total = all.length,
      checked_length = checked.length;

    if (role == 'dad') {
      if (me.is(':checked')) {
        all.prop('checked', true);
      } else {
        all.prop('checked', false);
      }
    } else {
      if (checked_length >= total) {
        dad.prop('checked', true);
      } else {
        dad.prop('checked', false);
      }
    }
  });
});

$("#table-1").dataTable({
  "order": [],  // Disable initial sorting to preserve Django's order_by
  "columnDefs": [
    { "orderable": false, "targets": "_all" }  // Columns remain unsortable by default
  ],
  "searching": true,  // Ensure the search bar is still enabled
  "paging": true,     // Ensure pagination is enabled
  "info": true        // Ensure table information display (e.g., showing X of Y entries) is enabled
});

$("#table-2").dataTable({
  "order": [[1, "asc"]],  // Apply sorting only on table-2, if needed
  "columnDefs": [
    { "orderable": false, "targets": [0, 2, 3] }
  ]
});

$('#save-stage').DataTable({
  "scrollX": true,
  stateSave: true
});

$('#tableExport').DataTable({
  dom: 'Bfrtip',
  buttons: [
    'copy', 'csv', 'excel', 'pdf', 'print'
  ]
});
