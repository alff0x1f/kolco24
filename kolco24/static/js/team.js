$( "#dist_header" ).click(function() {
    switch (dist) {
        case "6h":
            dist = "12h";
            $("#dist_header").text("12ч");
            break;
        case "12h":
            dist = "24h";
            $("#dist_header").text("24h");
            break;
        case "24h":
            dist = "6h";
            $("#dist_header").text("6h");
            break;
        default:
            break;
    }
    // alert( "Handler for .click() called." );
});

function set_ucount(count) {
    ucount = count;
    $("#ucountlabel").text(count);
    $("#sumlabel").text(count * cost);
    // hide members:
    if (count < 6) {
        $("#member6").addClass('d-none');
    } 
    if (count < 5) {
        $("#member5").addClass('d-none');
    }
    if (count < 4) {
        $("#member4").addClass('d-none');
    }
    if (count < 3) {
        $("#member3").addClass('d-none');
    }
    // show members and hide controls on top controls
    if (count >= 3){
        $("#member3").removeClass('d-none');
        $("#add_member3").addClass('d-none');
    }
    if (count >= 4){
        $("#member4").removeClass('d-none');
        $("#add_member4").addClass('d-none');
        $("#del_member3").addClass('d-none');
    }
    if (count >= 5){
        $("#member5").removeClass('d-none');
        $("#add_member5").addClass('d-none');
        $("#del_member4").addClass('d-none');
    }
    if (count >= 6){
        $("#member6").removeClass('d-none');
        $("#add_member6").addClass('d-none');
        $("#del_member5").addClass('d-none');
    }
    // show controls on last item
    if (count == 2){
        $("#del_member2").removeClass('d-none');
        $("#add_member3").removeClass('d-none');
    }
    if (count == 3){
        $("#del_member3").removeClass('d-none');
        $("#add_member4").removeClass('d-none');
    }
    if (count == 4){
        $("#del_member4").removeClass('d-none');
        $("#add_member5").removeClass('d-none');
    }
    if (count == 5){
        $("#del_member5").removeClass('d-none');
        $("#add_member6").removeClass('d-none');
    }
    if (count == 6){
        $("#del_member6").removeClass('d-none');
    }
  }